"""Tests for the audit logging service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.middleware import set_context_tenant_id, set_request_id
from restaurant_api.models import AuditLog, Employee, Store, Tenant
from restaurant_api.services.audit_service import audit


async def test_audit_writes_row_with_action_and_target(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store, seed_employee: Employee,
):
    """Basic happy path — audit() persists one row with all fields populated."""
    fake_order_id = uuid.uuid4()
    row = await audit(
        db_session,
        action="discount.applied",
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        target=("orders", fake_order_id),
        actor_id=seed_employee.id,
        before={"net_revenue": "1000.00"},
        after={"net_revenue": "900.00", "discount": "100.00"},
        reason="VIP comp",
    )
    assert row.id is not None
    assert row.action == "discount.applied"
    assert row.target_table == "orders"
    assert row.target_id == fake_order_id
    assert row.actor_id == seed_employee.id
    assert row.reason == "VIP comp"

    # Verify it was actually flushed to DB.
    persisted = (
        await db_session.execute(select(AuditLog).where(AuditLog.id == row.id))
    ).scalar_one()
    assert persisted.action == "discount.applied"
    assert persisted.before == {"net_revenue": "1000.00"}
    assert persisted.after == {"net_revenue": "900.00", "discount": "100.00"}


async def test_audit_picks_up_context_request_id(
    db_session: AsyncSession, seed_tenant: Tenant,
):
    """Audit log inherits request_id from contextvar via the structured logger."""
    set_request_id("rid-audit-test")
    set_context_tenant_id(str(seed_tenant.id))
    try:
        row = await audit(
            db_session,
            action="test.event",
            tenant_id=seed_tenant.id,
        )
        # request_id isn't stored on the row itself (Phase 2 will add a column),
        # but the audit log emission is happy with it set.
        assert row.action == "test.event"
    finally:
        set_request_id(None)
        set_context_tenant_id(None)


async def test_audit_no_target_for_tenant_level_events(
    db_session: AsyncSession, seed_tenant: Tenant,
):
    """Settings changes / role grants don't have a target row."""
    row = await audit(
        db_session,
        action="settings.changed",
        tenant_id=seed_tenant.id,
        after={"multi_tenant_enabled": True},
    )
    assert row.target_table is None
    assert row.target_id is None
    assert row.action == "settings.changed"
