"""會員生命週期引擎 — service-level tests (scoped to seed_tenant).

Covers the four retention levers:
- tier recompute off rolling-window spend (promote + downgrade), idempotent
- welcome bonus on join, idempotent
- birthday gift, idempotent within the year
- dormant win-back, idempotent within the calendar month
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
    CustomerTier,
    MenuItem,
    Order,
    OrderLine,
    OrderStatus,
    Store,
    Tenant,
)
from restaurant_api.services import membership_service

pytestmark = pytest.mark.asyncio


def _safe_birthdate(month: int, day: int) -> date:
    """A birthdate with the given month/day; leap-safe for Feb 29."""
    year = 2000 if (month, day) == (2, 29) else 1990
    return date(year, month, day)


async def _make_customer(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    tier: CustomerTier = CustomerTier.REGULAR,
    birthdate: date | None = None,
    last_visit_at: datetime | None = None,
    line_user_id: str | None = None,
) -> Customer:
    cust = Customer(
        tenant_id=tenant_id,
        display_name="測試客",
        tier=tier,
        birthdate=birthdate,
        last_visit_at=last_visit_at,
        line_user_id=line_user_id or f"U{uuid.uuid4().hex[:16]}",
    )
    session.add(cust)
    await session.flush()
    return cust


async def _closed_order(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    menu_item_id: uuid.UUID,
    customer_id: uuid.UUID,
    line_total: Decimal,
    closed_at: datetime,
) -> None:
    order = Order(
        tenant_id=tenant_id,
        store_id=store_id,
        customer_id=customer_id,
        order_no=f"T{uuid.uuid4().hex[:10]}",
        business_date=closed_at.date(),
        opened_at=closed_at,
        closed_at=closed_at,
        status=OrderStatus.CLOSED,
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderLine(
            tenant_id=tenant_id,
            order_id=order.id,
            menu_item_id=menu_item_id,
            qty=Decimal("1"),
            unit_price=line_total,
            line_total=line_total,
        )
    )
    await session.flush()


async def _balance(session: AsyncSession, customer_id: uuid.UUID) -> Decimal:
    return Decimal(
        (
            await session.execute(
                select(func.coalesce(func.sum(CustomerPointsLedger.delta), 0)).where(
                    CustomerPointsLedger.customer_id == customer_id
                )
            )
        ).scalar_one()
    )


# ── Tier recompute ──────────────────────────────────────────────────────────


async def test_tier_recompute_promotes_on_window_spend(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
) -> None:
    cust = await _make_customer(db_session, seed_tenant.id)
    now = datetime.now(UTC)
    await _closed_order(
        db_session,
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        menu_item_id=seed_menu_item.id,
        customer_id=cust.id,
        line_total=Decimal("5000"),  # → GOLD (≥4000)
        closed_at=now - timedelta(days=10),
    )

    changes = await membership_service.recompute_tiers(
        db_session, tenant_id=seed_tenant.id, now=now
    )

    mine = [c for c in changes if c.customer.id == cust.id]
    assert len(mine) == 1
    assert mine[0].old_tier == CustomerTier.REGULAR
    assert mine[0].new_tier == CustomerTier.GOLD
    assert mine[0].is_upgrade is True
    assert cust.tier == CustomerTier.GOLD


async def test_tier_recompute_downgrades_when_window_empty(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _make_customer(db_session, seed_tenant.id, tier=CustomerTier.GOLD)

    changes = await membership_service.recompute_tiers(
        db_session, tenant_id=seed_tenant.id
    )

    mine = [c for c in changes if c.customer.id == cust.id]
    assert len(mine) == 1
    assert mine[0].new_tier == CustomerTier.REGULAR
    assert mine[0].is_upgrade is False
    assert cust.tier == CustomerTier.REGULAR


async def test_tier_recompute_ignores_orders_outside_window(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
) -> None:
    cust = await _make_customer(db_session, seed_tenant.id)
    now = datetime.now(UTC)
    await _closed_order(
        db_session,
        tenant_id=seed_tenant.id,
        store_id=seed_store.id,
        menu_item_id=seed_menu_item.id,
        customer_id=cust.id,
        line_total=Decimal("9000"),
        closed_at=now - timedelta(days=200),  # older than the 180-day window
    )

    await membership_service.recompute_tiers(
        db_session, tenant_id=seed_tenant.id, now=now
    )
    assert cust.tier == CustomerTier.REGULAR


# ── Welcome bonus ────────────────────────────────────────────────────────────


async def test_welcome_bonus_grants_once(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _make_customer(db_session, seed_tenant.id)

    first = await membership_service.grant_welcome_bonus(db_session, cust)
    assert first is True
    assert await _balance(db_session, cust.id) == membership_service.WELCOME_POINTS

    second = await membership_service.grant_welcome_bonus(db_session, cust)
    assert second is False
    assert await _balance(db_session, cust.id) == membership_service.WELCOME_POINTS


# ── Birthday gift ────────────────────────────────────────────────────────────


async def test_birthday_gift_grants_once_per_year(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    today = membership_service._tpe_today()
    # A birthday today (year doesn't matter — only month/day).
    bday = _safe_birthdate(today.month, today.day)
    cust = await _make_customer(db_session, seed_tenant.id, birthdate=bday)

    gifted = await membership_service.grant_birthday_gifts(
        db_session, tenant_id=seed_tenant.id
    )
    assert any(c.id == cust.id for c in gifted)
    assert await _balance(db_session, cust.id) == membership_service.BIRTHDAY_POINTS

    again = await membership_service.grant_birthday_gifts(
        db_session, tenant_id=seed_tenant.id
    )
    assert all(c.id != cust.id for c in again)
    assert await _balance(db_session, cust.id) == membership_service.BIRTHDAY_POINTS


async def test_birthday_gift_skips_non_birthday(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    today = membership_service._tpe_today()
    not_today = today + timedelta(days=3)
    cust = await _make_customer(
        db_session, seed_tenant.id, birthdate=_safe_birthdate(not_today.month, not_today.day)
    )
    gifted = await membership_service.grant_birthday_gifts(
        db_session, tenant_id=seed_tenant.id
    )
    assert all(c.id != cust.id for c in gifted)


# ── Dormant win-back ─────────────────────────────────────────────────────────


async def test_dormant_winback_grants_once_per_period(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    now = datetime.now(UTC)
    cust = await _make_customer(
        db_session, seed_tenant.id, last_visit_at=now - timedelta(days=45)
    )

    nudged = await membership_service.grant_dormant_winback(
        db_session, tenant_id=seed_tenant.id, now=now
    )
    assert any(c.id == cust.id for c in nudged)
    assert (
        await _balance(db_session, cust.id)
        == membership_service.DORMANT_COMEBACK_POINTS
    )

    again = await membership_service.grant_dormant_winback(
        db_session, tenant_id=seed_tenant.id, now=now
    )
    assert all(c.id != cust.id for c in again)
    assert (
        await _balance(db_session, cust.id)
        == membership_service.DORMANT_COMEBACK_POINTS
    )


async def test_dormant_winback_skips_recent_visitors(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    now = datetime.now(UTC)
    cust = await _make_customer(
        db_session, seed_tenant.id, last_visit_at=now - timedelta(days=5)
    )
    nudged = await membership_service.grant_dormant_winback(
        db_session, tenant_id=seed_tenant.id, now=now
    )
    assert all(c.id != cust.id for c in nudged)
