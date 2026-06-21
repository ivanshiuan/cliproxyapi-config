"""連續回訪 Streak — service unit tests + end-to-end points multiplier.

Unit: streak counts consecutive CLOSED-order business dates and breaks on a gap.
E2E: closing orders on consecutive days lifts the points multiplier (verified
through the real ``orders_service.close_order`` accrual path).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.models import (
    Customer,
    CustomerPointsLedger,
    MenuItem,
    Order,
    OrderLine,
    OrderStatus,
    Store,
    Tenant,
)
from restaurant_api.services import orders_service, streak_service

pytestmark = pytest.mark.asyncio


async def _customer(session: AsyncSession, tenant_id: uuid.UUID, **kw) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="回訪客", **kw)
    session.add(cust)
    await session.flush()
    return cust


async def _closed_order_on(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    customer_id: uuid.UUID,
    business_date: date,
) -> None:
    order = Order(
        tenant_id=tenant_id,
        store_id=store_id,
        customer_id=customer_id,
        order_no=f"S{uuid.uuid4().hex[:10]}",
        business_date=business_date,
        opened_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
        status=OrderStatus.CLOSED,
    )
    session.add(order)
    await session.flush()


# ── multiplier schedule ──────────────────────────────────────────────────────


async def test_multiplier_schedule() -> None:
    assert streak_service.streak_multiplier(1) == Decimal("1.0")
    assert streak_service.streak_multiplier(2) == Decimal("1.0")
    assert streak_service.streak_multiplier(3) == Decimal("1.1")
    assert streak_service.streak_multiplier(5) == Decimal("1.2")
    assert streak_service.streak_multiplier(7) == Decimal("1.5")
    assert streak_service.streak_multiplier(30) == Decimal("1.5")


# ── streak computation ───────────────────────────────────────────────────────


async def test_streak_counts_consecutive_days(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    today = date(2026, 6, 19)
    for d in (today, today - timedelta(days=1), today - timedelta(days=2)):
        await _closed_order_on(
            db_session, tenant_id=seed_tenant.id, store_id=seed_store.id,
            customer_id=cust.id, business_date=d,
        )
    streak = await streak_service.compute_visit_streak(
        db_session, customer_id=cust.id, tenant_id=seed_tenant.id, as_of=today
    )
    assert streak == 3


async def test_streak_breaks_on_gap(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    today = date(2026, 6, 19)
    # today + yesterday, then a gap, then 4 days ago — streak ending today is 2.
    for d in (today, today - timedelta(days=1), today - timedelta(days=4)):
        await _closed_order_on(
            db_session, tenant_id=seed_tenant.id, store_id=seed_store.id,
            customer_id=cust.id, business_date=d,
        )
    streak = await streak_service.compute_visit_streak(
        db_session, customer_id=cust.id, tenant_id=seed_tenant.id, as_of=today
    )
    assert streak == 2


async def test_same_day_multiple_orders_count_once(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    today = date(2026, 6, 19)
    for _ in range(3):
        await _closed_order_on(
            db_session, tenant_id=seed_tenant.id, store_id=seed_store.id,
            customer_id=cust.id, business_date=today,
        )
    streak = await streak_service.compute_visit_streak(
        db_session, customer_id=cust.id, tenant_id=seed_tenant.id, as_of=today
    )
    assert streak == 1


async def test_no_orders_is_zero(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    streak = await streak_service.compute_visit_streak(
        db_session, customer_id=cust.id, tenant_id=seed_tenant.id, as_of=date(2026, 6, 19)
    )
    assert streak == 0


# ── end-to-end: multiplier applied through real close_order ──────────────────


async def _earned(session: AsyncSession, customer_id: uuid.UUID) -> Decimal:
    return Decimal(
        (
            await session.execute(
                select(func.coalesce(func.sum(CustomerPointsLedger.delta), 0)).where(
                    CustomerPointsLedger.customer_id == customer_id,
                    CustomerPointsLedger.reason == "order.earn",
                )
            )
        ).scalar_one()
    )


async def test_close_applies_streak_multiplier(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
) -> None:
    """On the 3rd consecutive day, a 1000 TWD order earns 10 * 1.1 = 11 points."""
    cust = await _customer(db_session, seed_tenant.id)
    today = date(2026, 6, 19)
    # Two prior consecutive days already visited.
    for d in (today - timedelta(days=2), today - timedelta(days=1)):
        await _closed_order_on(
            db_session, tenant_id=seed_tenant.id, store_id=seed_store.id,
            customer_id=cust.id, business_date=d,
        )

    # Real order for today, closed via the service → streak now 3 → x1.1.
    order = Order(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        customer_id=cust.id,
        order_no=f"S{uuid.uuid4().hex[:10]}",
        business_date=today,
        opened_at=datetime.now(UTC),
        status=OrderStatus.OPEN,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderLine(
            tenant_id=seed_tenant.id,
            order_id=order.id,
            menu_item_id=seed_menu_item.id,
            qty=Decimal("1"),
            unit_price=Decimal("1000"),
            line_total=Decimal("1000"),
        )
    )
    await db_session.flush()

    await orders_service.close_order(
        db_session, order.id, seed_tenant.id, datetime.now(UTC)
    )

    # Base 1000 * 1% * 1.0 (REGULAR) = 10; streak 3 → x1.1 = 11.
    earn_row = (
        await db_session.execute(
            select(CustomerPointsLedger.delta).where(
                CustomerPointsLedger.source_order_id == order.id,
                CustomerPointsLedger.reason == "order.earn",
            )
        )
    ).scalar_one()
    assert earn_row == Decimal("11")


async def test_zero_revenue_order_writes_no_points(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
) -> None:
    """A fully-comped (0 TWD) order earns no points — and skips the streak path."""
    cust = await _customer(db_session, seed_tenant.id)
    order = Order(
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        customer_id=cust.id,
        order_no=f"S{uuid.uuid4().hex[:10]}",
        business_date=date(2026, 6, 19),
        opened_at=datetime.now(UTC),
        status=OrderStatus.OPEN,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderLine(
            tenant_id=seed_tenant.id,
            order_id=order.id,
            menu_item_id=seed_menu_item.id,
            qty=Decimal("1"),
            unit_price=Decimal("0"),
            line_total=Decimal("0"),
        )
    )
    await db_session.flush()

    await orders_service.close_order(
        db_session, order.id, seed_tenant.id, datetime.now(UTC)
    )

    n = (
        await db_session.execute(
            select(func.count()).select_from(CustomerPointsLedger).where(
                CustomerPointsLedger.source_order_id == order.id
            )
        )
    ).scalar_one()
    assert n == 0
