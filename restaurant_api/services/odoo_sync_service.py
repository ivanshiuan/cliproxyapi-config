"""Push received purchase orders into Odoo as vendor bills (accounts payable).

This is the operations -> finance bridge from ``docs/20``: restaurant_api owns
*what was purchased*; Odoo owns the *accounting* of it. For every purchase
order that has been received but not yet booked, this service upserts the
supplier as an Odoo AP partner and creates a **draft** vendor bill, then stamps
``odoo_move_id`` / ``odoo_synced_at`` on the PO so it is never pushed twice.

Reliability contract:

* Idempotent twice over — the query only picks up POs with
  ``odoo_synced_at IS NULL``, and the client itself performs find-before-create
  on the ``[src:<po_id>]`` marker, so even a crash between "created in Odoo"
  and "committed here" cannot duplicate a draft.
* Per-item error isolation — one bad PO never aborts the batch. Each failure
  bumps ``odoo_sync_attempts`` and records ``odoo_last_sync_error``.
* Dead-letter — a PO that fails ``MAX_SYNC_ATTEMPTS`` times is excluded from
  automatic retry and surfaced by ``reconcile_sync_status`` for a human.
* A missing supplier master row is a hard refusal, not a silent fallback: we
  never book an AP liability against an unverified party.
* Draft-by-default — bills are created but never posted to the ledger unless
  the client was built with ``allow_auto_post`` (``ODOO_ALLOW_AUTO_POST``).
* Every run carries a ``run_id`` that appears in each structured log line, so
  a night's batch can be traced end to end.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_sessionmaker
from ..integrations.odoo import (
    OdooClient,
    PurchaseBill,
    SupplierRecord,
    get_odoo,
    purchase_to_vendor_bill,
)
from ..models import PurchaseOrder, PurchaseOrderStatus, Supplier

logger = logging.getLogger("restaurant_api.services.odoo_sync")

# After this many failed pushes a PO is dead-lettered: no more automatic
# retries; it shows up in reconcile_sync_status()["dead_letter"] instead.
MAX_SYNC_ATTEMPTS = 5


@dataclass(frozen=True)
class SyncReport:
    """Outcome of one sync run."""

    run_id: str = ""
    considered: int = 0
    pushed: int = 0
    failed: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0


async def sync_purchase_orders(
    tenant_id: uuid.UUID | None = None,
    session: AsyncSession | None = None,
    odoo: OdooClient | None = None,
    limit: int = 200,
) -> SyncReport:
    """Book received-but-unsynced purchase orders into Odoo AP.

    ``session`` / ``odoo`` let tests inject a rolled-back session and a
    ``StubOdooClient``; in production both default to the process singletons.
    """
    client = odoo or get_odoo()
    if session is not None:
        return await _sync(session, client, tenant_id, limit)
    session_local = get_sessionmaker()
    async with session_local() as own_session:
        report = await _sync(own_session, client, tenant_id, limit)
        await own_session.commit()
        return report


async def _sync(
    session: AsyncSession,
    client: OdooClient,
    tenant_id: uuid.UUID | None,
    limit: int,
) -> SyncReport:
    run_id = uuid.uuid4().hex[:12]
    tz = ZoneInfo(get_settings().default_timezone)

    stmt = (
        select(PurchaseOrder)
        .where(
            PurchaseOrder.received_at.is_not(None),
            PurchaseOrder.odoo_synced_at.is_(None),
            PurchaseOrder.status == PurchaseOrderStatus.RECEIVED,
            PurchaseOrder.odoo_sync_attempts < MAX_SYNC_ATTEMPTS,
        )
        .order_by(PurchaseOrder.received_at)
        .limit(limit)
    )
    if tenant_id is not None:
        stmt = stmt.where(PurchaseOrder.tenant_id == tenant_id)

    orders = list((await session.execute(stmt)).scalars().all())
    pushed = failed = 0

    for po in orders:
        try:
            supplier = await session.get(Supplier, po.supplier_id)
            if supplier is None or supplier.deleted_at is not None:
                # Hard refusal: never book an AP liability against a missing or
                # deactivated supplier. The PO goes to the failure path and,
                # after MAX attempts, the dead-letter queue.
                raise LookupError(
                    f"supplier {po.supplier_id} is missing or deactivated; refusing to book AP"
                )
            # The received timestamp is UTC in the DB; book the bill on the
            # local (Asia/Taipei) accounting date.
            received = po.received_at or po.ordered_at
            occurred_on = received.astimezone(tz).date()

            await client.upsert_supplier(
                SupplierRecord(
                    ref=supplier.code,
                    name=supplier.name,
                    tax_id=supplier.tax_id,
                    payment_terms=supplier.payment_terms,
                )
            )

            entry = purchase_to_vendor_bill(
                PurchaseBill(
                    source_id=str(po.id),
                    supplier_ref=supplier.code,
                    invoice_number=po.invoice_number or po.po_number,
                    occurred_on=occurred_on,
                    subtotal=po.subtotal,
                    tax_amount=po.tax_amount,
                )
            )
            move_id = await client.create_vendor_bill(entry)
            po.odoo_move_id = move_id
            po.odoo_synced_at = datetime.now(UTC)
            po.odoo_last_sync_error = None
            pushed += 1
            logger.info(
                "odoo_sync.pushed",
                extra={"run_id": run_id, "po_id": str(po.id), "odoo_move_id": move_id},
            )
        except Exception as exc:
            failed += 1
            po.odoo_sync_attempts = (po.odoo_sync_attempts or 0) + 1
            po.odoo_last_sync_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.exception(
                "odoo_sync.failed",
                extra={
                    "run_id": run_id,
                    "po_id": str(po.id),
                    "attempts": po.odoo_sync_attempts,
                    "dead_lettered": po.odoo_sync_attempts >= MAX_SYNC_ATTEMPTS,
                },
            )

    logger.info(
        "odoo_sync.complete",
        extra={
            "run_id": run_id,
            "considered": len(orders),
            "pushed": pushed,
            "failed": failed,
        },
    )
    return SyncReport(run_id=run_id, considered=len(orders), pushed=pushed, failed=failed)


async def reconcile_sync_status(
    session: AsyncSession,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Count POs by sync state — the reconciliation view for humans/dashboards.

    ``pending`` should drain to zero every night; ``dead_letter`` > 0 means a
    PO needs manual attention (fix the data, reset ``odoo_sync_attempts``).
    """

    async def _count(*conds) -> int:
        stmt = select(func.count()).select_from(PurchaseOrder).where(*conds)
        if tenant_id is not None:
            stmt = stmt.where(PurchaseOrder.tenant_id == tenant_id)
        return int((await session.execute(stmt)).scalar_one())

    synced = await _count(PurchaseOrder.odoo_synced_at.is_not(None))
    pending = await _count(
        PurchaseOrder.received_at.is_not(None),
        PurchaseOrder.odoo_synced_at.is_(None),
        PurchaseOrder.status == PurchaseOrderStatus.RECEIVED,
        PurchaseOrder.odoo_sync_attempts < MAX_SYNC_ATTEMPTS,
    )
    dead_letter = await _count(
        PurchaseOrder.odoo_synced_at.is_(None),
        PurchaseOrder.odoo_sync_attempts >= MAX_SYNC_ATTEMPTS,
    )
    not_received = await _count(PurchaseOrder.received_at.is_(None))
    return {
        "synced": synced,
        "pending": pending,
        "dead_letter": dead_letter,
        "not_received": not_received,
    }


__all__ = ["MAX_SYNC_ATTEMPTS", "SyncReport", "reconcile_sync_status", "sync_purchase_orders"]
