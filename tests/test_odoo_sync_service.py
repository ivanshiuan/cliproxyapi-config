"""Odoo AP sync — received purchase orders become vendor bills.

Scoped to ``seed_tenant``; uses the in-memory ``StubOdooClient`` so no live
Odoo is touched. Covers the happy path, idempotency, the draft/received gate,
and per-item error isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.odoo import JournalEntry, StubOdooClient
from restaurant_api.models import (
    PurchaseOrder,
    PurchaseOrderStatus,
    Store,
    Supplier,
    Tenant,
)
from restaurant_api.services.odoo_sync_service import (
    MAX_SYNC_ATTEMPTS,
    reconcile_sync_status,
    sync_purchase_orders,
)

pytestmark = pytest.mark.asyncio


async def _supplier(
    session: AsyncSession, tenant_id: uuid.UUID, *, code: str = "SUP-C"
) -> Supplier:
    s = Supplier(tenant_id=tenant_id, code=code, name="C 食材行", tax_id="12345678")
    session.add(s)
    await session.flush()
    return s


async def _po(
    session: AsyncSession,
    tenant: Tenant,
    store: Store,
    supplier: Supplier,
    *,
    received: bool = True,
    po_number: str | None = None,
    subtotal: Decimal = Decimal("1000"),
    tax: Decimal = Decimal("50"),
) -> PurchaseOrder:
    now = datetime.now(UTC)
    po = PurchaseOrder(
        tenant_id=tenant.id,
        store_id=store.id,
        supplier_id=supplier.id,
        po_number=po_number or f"PO-{uuid.uuid4().hex[:8]}",
        ordered_at=now,
        received_at=now if received else None,
        status=PurchaseOrderStatus.RECEIVED if received else PurchaseOrderStatus.DRAFT,
        subtotal=subtotal,
        tax_amount=tax,
        total=subtotal + tax,
        invoice_number="AB-12345678",
    )
    session.add(po)
    await session.flush()
    return po


async def test_sync_pushes_received_po_and_stamps_it(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    sup = await _supplier(db_session, seed_tenant.id)
    po = await _po(db_session, seed_tenant, seed_store, sup)
    stub = StubOdooClient()

    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)

    assert report.pushed == 1
    assert report.failed == 0
    assert po.odoo_move_id is not None
    assert po.odoo_synced_at is not None

    ops = [c["op"] for c in stub.calls]
    assert "upsert_supplier" in ops
    bills = [c for c in stub.calls if c["op"] == "create_vendor_bill"]
    assert len(bills) == 1
    assert bills[0]["external_id"] == str(po.id)
    assert bills[0]["posted"] is False  # draft — auto-post off by default
    entry = bills[0]["entry"]
    assert isinstance(entry, JournalEntry)
    assert entry.is_balanced()
    assert entry.total_credit() == Decimal("1050")  # subtotal + tax booked to AP


async def test_sync_is_idempotent(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    sup = await _supplier(db_session, seed_tenant.id)
    await _po(db_session, seed_tenant, seed_store, sup)
    stub = StubOdooClient()

    first = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)
    second = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)

    assert first.pushed == 1
    assert second.considered == 0  # already stamped -> not reconsidered
    assert second.pushed == 0


async def test_draft_po_is_not_synced(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    sup = await _supplier(db_session, seed_tenant.id)
    await _po(db_session, seed_tenant, seed_store, sup, received=False)
    stub = StubOdooClient()

    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)

    assert report.considered == 0
    assert stub.calls == []


class _FailOnStub(StubOdooClient):
    """Stub that raises on the vendor bill for one specific external_id."""

    fail_ext: str = ""

    async def create_vendor_bill(self, entry: JournalEntry, *, post: bool = False) -> str:
        if entry.external_id == self.fail_ext:
            raise RuntimeError("odoo unreachable")
        return await super().create_vendor_bill(entry, post=post)


async def test_per_item_error_isolation(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    sup = await _supplier(db_session, seed_tenant.id)
    bad = await _po(db_session, seed_tenant, seed_store, sup, po_number="PO-BAD")
    good = await _po(db_session, seed_tenant, seed_store, sup, po_number="PO-GOOD")
    stub = _FailOnStub()
    stub.fail_ext = str(bad.id)

    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)

    assert report.pushed == 1
    assert report.failed == 1
    assert bad.odoo_synced_at is None  # failed one stays pending for next run
    assert good.odoo_synced_at is not None
    # failure bookkeeping for the dead-letter path
    assert bad.odoo_sync_attempts == 1
    assert bad.odoo_last_sync_error is not None
    assert "RuntimeError" in bad.odoo_last_sync_error
    assert good.odoo_last_sync_error is None


async def test_soft_deleted_supplier_blocks_push(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    """AP is never booked against a deactivated supplier — hard refusal."""
    sup = await _supplier(db_session, seed_tenant.id)
    po = await _po(db_session, seed_tenant, seed_store, sup)
    sup.deleted_at = datetime.now(UTC)  # soft-delete the supplier
    stub = StubOdooClient()

    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)

    assert report.pushed == 0
    assert report.failed == 1
    assert po.odoo_synced_at is None
    assert po.odoo_last_sync_error is not None
    assert "deactivated" in po.odoo_last_sync_error
    assert stub.calls == []  # nothing reached Odoo


async def test_dead_lettered_po_is_excluded_from_retry(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    sup = await _supplier(db_session, seed_tenant.id)
    po = await _po(db_session, seed_tenant, seed_store, sup)
    po.odoo_sync_attempts = MAX_SYNC_ATTEMPTS  # already exhausted
    stub = StubOdooClient()

    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=stub)

    assert report.considered == 0  # dead-lettered — not retried automatically
    assert stub.calls == []


async def test_reconcile_sync_status_counts(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    sup = await _supplier(db_session, seed_tenant.id)
    synced_po = await _po(db_session, seed_tenant, seed_store, sup, po_number="PO-S")
    await _po(db_session, seed_tenant, seed_store, sup, po_number="PO-P")  # pending
    dead_po = await _po(db_session, seed_tenant, seed_store, sup, po_number="PO-D")
    await _po(db_session, seed_tenant, seed_store, sup, po_number="PO-N", received=False)

    synced_po.odoo_synced_at = datetime.now(UTC)
    synced_po.odoo_move_id = "42"
    dead_po.odoo_sync_attempts = MAX_SYNC_ATTEMPTS
    await db_session.flush()

    counts = await reconcile_sync_status(db_session, tenant_id=seed_tenant.id)
    assert counts == {"synced": 1, "pending": 1, "dead_letter": 1, "not_received": 1}


async def test_report_carries_run_id(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    report = await sync_purchase_orders(
        tenant_id=seed_tenant.id, session=db_session, odoo=StubOdooClient()
    )
    assert len(report.run_id) == 12  # traceable correlation id on every run
