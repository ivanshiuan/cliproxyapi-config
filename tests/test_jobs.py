"""Tests for the background-job framework + 3 nightly jobs.

Each job accepts an optional ``session`` so tests can inject the conftest
savepoint-rolled session — no real DB writes leak between tests, but the
job's actual SQL runs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.jobs.cogs_variance import run_cogs_variance_check
from restaurant_api.jobs.expiry_warning import run_expiry_warning
from restaurant_api.jobs.points_expire import run_points_expire
from restaurant_api.models import (
    AuditLog,
    Customer,
    CustomerPointsLedger,
    CustomerTier,
    Ingredient,
    MovementType,
    StockMovement,
    Store,
    Tenant,
)


async def test_expiry_warning_writes_audit_for_near_expiry(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store,
):
    """An ingredient with expiry in 2 days AND positive on-hand should warn."""
    ing = Ingredient(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        name="Test Lettuce",
        unit="g",
        standard_cost_per_unit=Decimal("0.12"),
        lot_no="LT-EXPIRING",
        expiry_date=date.today() + timedelta(days=2),
        is_active=True,
    )
    db_session.add(ing)
    await db_session.flush()
    db_session.add(
        StockMovement(
            tenant_id=seed_tenant.id,
            store_id=seed_store.id,
            ingredient_id=ing.id,
            movement_type=MovementType.PURCHASE,
            qty=Decimal("500"),
            occurred_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    n_written = await run_expiry_warning(session=db_session)
    assert n_written >= 1

    audit_rows = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.action == "expiry.warning")
            .where(AuditLog.target_id == ing.id)
        )
    ).scalars().all()
    assert len(audit_rows) >= 1
    row = audit_rows[0]
    assert row.after is not None
    assert row.after["lot_no"] == "LT-EXPIRING"
    assert row.after["days_left"] == 2


async def test_expiry_warning_idempotent_within_day(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store,
):
    """Re-running the job same day shouldn't double-write."""
    ing = Ingredient(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        name="Test Idempotent",
        unit="g",
        standard_cost_per_unit=Decimal("0.20"),
        lot_no="LT-IDEMP",
        expiry_date=date.today() + timedelta(days=1),
        is_active=True,
    )
    db_session.add(ing)
    await db_session.flush()
    db_session.add(
        StockMovement(
            tenant_id=seed_tenant.id, store_id=seed_store.id,
            ingredient_id=ing.id, movement_type=MovementType.PURCHASE,
            qty=Decimal("100"), occurred_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    first = await run_expiry_warning(session=db_session)
    second = await run_expiry_warning(session=db_session)
    assert first >= 1
    assert second == 0


async def test_points_expire_writes_reversing_row(
    db_session: AsyncSession, seed_tenant: Tenant,
):
    """A points grant with passed expires_at should produce a -delta reversal."""
    customer = Customer(
        tenant_id=seed_tenant.id,
        display_name="Test Expiry Customer",
        tier=CustomerTier.SILVER,
        points_balance=Decimal("100"),
    )
    db_session.add(customer)
    await db_session.flush()

    grant = CustomerPointsLedger(
        tenant_id=seed_tenant.id,
        customer_id=customer.id,
        delta=Decimal("100"),
        reason="order.earn",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(grant)
    await db_session.flush()

    n = await run_points_expire(session=db_session)
    assert n >= 1

    reversals = (
        await db_session.execute(
            select(CustomerPointsLedger)
            .where(CustomerPointsLedger.customer_id == customer.id)
            .where(CustomerPointsLedger.reason == "expire.batch")
        )
    ).scalars().all()
    assert any(r.delta == Decimal("-100") for r in reversals)


async def test_points_expire_idempotent(
    db_session: AsyncSession, seed_tenant: Tenant,
):
    """Second run doesn't re-reverse the same grant."""
    customer = Customer(
        tenant_id=seed_tenant.id,
        display_name="Idemp Customer",
        tier=CustomerTier.SILVER,
        points_balance=Decimal("50"),
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add(CustomerPointsLedger(
        tenant_id=seed_tenant.id,
        customer_id=customer.id,
        delta=Decimal("50"),
        reason="order.earn",
        expires_at=datetime.now(UTC) - timedelta(days=2),
    ))
    await db_session.flush()

    first = await run_points_expire(session=db_session)
    second = await run_points_expire(session=db_session)
    assert first >= 1
    assert second == 0


async def test_cogs_variance_handles_empty_day_gracefully(db_session: AsyncSession):
    """If no closed orders on the target day, return 0 (don't crash)."""
    far_date = date(2020, 1, 1)
    n = await run_cogs_variance_check(target_date=far_date, session=db_session)
    assert n == 0
