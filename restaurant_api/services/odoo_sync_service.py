"""Push received purchase orders into Odoo as vendor bills (accounts payable).

This is the operations -> finance bridge from ``docs/20``: restaurant_api owns
*what was purchased*; Odoo owns the *accounting* of it. For every purchase
order that has been received but not yet booked, this service upserts the
supplier as an Odoo AP partner and creates a **draft** vendor bill, then stamps
``odoo_move_id`` / ``odoo_synced_at`` on the PO so it is never pushed twice.

Idempotency is structural: the query only picks up POs with
``odoo_synced_at IS NULL``. A crash mid-batch leaves already-stamped POs done
and the rest pending for the next run. Per-item failures are isolated -- one
bad PO does not abort the batch.

Draft-by-default: bills are created but not posted to the ledger unless the
Odoo client was built with ``allow_auto_post`` (``ODOO_ALLOW_AUTO_POST``). That
is the one-time policy switch from ``docs/20`` -- humans review drafts in Odoo
until you opt into full automation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
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


@dataclass(frozen=True)
class SyncReport:
    """Outcome of one sync run."""

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
    tz = ZoneInfo(get_settings().default_timezone)

    stmt = (
        select(PurchaseOrder)
        .where(
            PurchaseOrder.received_at.is_not(None),
            PurchaseOrder.odoo_synced_at.is_(None),
            PurchaseOrder.status == PurchaseOrderStatus.RECEIVED,
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
            supplier_ref = supplier.code if supplier else str(po.supplier_id)
            # The received timestamp is UTC in the DB; book the bill on the
            # local (Asia/Taipei) accounting date.
            received = po.received_at or po.ordered_at
            occurred_on = received.astimezone(tz).date()

            if supplier is not None:
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
                    supplier_ref=supplier_ref,
                    invoice_number=po.invoice_number or po.po_number,
                    occurred_on=occurred_on,
                    subtotal=po.subtotal,
                    tax_amount=po.tax_amount,
                )
            )
            move_id = await client.create_vendor_bill(entry)
            po.odoo_move_id = move_id
            po.odoo_synced_at = datetime.now(UTC)
            pushed += 1
            logger.info(
                "odoo_sync.pushed",
                extra={"po_id": str(po.id), "odoo_move_id": move_id},
            )
        except Exception:
            failed += 1
            logger.exception("odoo_sync.failed", extra={"po_id": str(po.id)})

    logger.info(
        "odoo_sync.complete",
        extra={"considered": len(orders), "pushed": pushed, "failed": failed},
    )
    return SyncReport(considered=len(orders), pushed=pushed, failed=failed)


__all__ = ["SyncReport", "sync_purchase_orders"]
