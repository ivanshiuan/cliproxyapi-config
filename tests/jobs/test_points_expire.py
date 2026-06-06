"""Integration tests for the nightly points_expire job + the orders close
path that stamps ``expires_at`` on earn rows.

End-to-end:
  1. Close order → earn row written with expires_at = closed_at + N days
  2. Time passes (we backdate the row in the test)
  3. Job runs → negative ledger row "expire.batch", customer balance
     decremented (capped so it never goes negative)
  4. Job re-run is a no-op (idempotency via "expired_source:<id>" marker)
  5. Future-dated expires_at not expired yet → no-op
  6. None expires_at (legacy mode) → never expired
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.jobs.points_expire import run_points_expire
from restaurant_api.models import (
    Customer,
    CustomerPointsLedger,
    CustomerTier,
    Tenant,
)

pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


async def _make_customer_with_earn(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    *,
    earn: Decimal,
    expires_at: datetime | None,
    starting_balance: Decimal | None = None,
) -> tuple[Customer, CustomerPointsLedger]:
    """Create a customer + a single earn ledger row with the given expiry."""
    cust = Customer(
        tenant_id=seed_tenant.id,
        display_name=f"P{uuid.uuid4().hex[:6]}",
        tier=CustomerTier.REGULAR,
        points_balance=starting_balance if starting_balance is not None else earn,
    )
    db_session.add(cust)
    await db_session.flush()
    ledger = CustomerPointsLedger(
        tenant_id=seed_tenant.id,
        customer_id=cust.id,
        delta=earn,
        reason="order.earn",
        expires_at=expires_at,
        note=f"test earn {earn}",
    )
    db_session.add(ledger)
    await db_session.flush()
    return cust, ledger


# ──────────────────────────────────────────────────────────────────────────
# Expiry job behaviour
# ──────────────────────────────────────────────────────────────────────────


async def test_expired_earn_row_writes_reversal_and_decrements_balance(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    cust, _ = await _make_customer_with_earn(
        db_session, seed_tenant, earn=Decimal("50"), expires_at=yesterday
    )

    n = await run_points_expire(session=db_session)
    assert n == 1

    # One reversal row added; net delta = 0.
    rows = (
        await db_session.execute(
            select(CustomerPointsLedger).where(
                CustomerPointsLedger.customer_id == cust.id
            )
        )
    ).scalars().all()
    deltas = sorted(r.delta for r in rows)
    assert deltas == [Decimal("-50"), Decimal("50")]
    expire_row = next(r for r in rows if r.reason == "expire.batch")
    assert expire_row.delta == Decimal("-50")
    assert expire_row.note is not None
    assert expire_row.note.startswith("expired_source:")

    # Balance went 50 → 0.
    await db_session.refresh(cust)
    assert cust.points_balance == Decimal("0")


async def test_future_expires_at_not_expired_yet(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    cust, _ = await _make_customer_with_earn(
        db_session, seed_tenant, earn=Decimal("30"), expires_at=tomorrow
    )

    n = await run_points_expire(session=db_session)
    assert n == 0

    await db_session.refresh(cust)
    assert cust.points_balance == Decimal("30")


async def test_none_expires_at_never_expires(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Legacy / never-expires rows must be untouched."""
    cust, _ = await _make_customer_with_earn(
        db_session, seed_tenant, earn=Decimal("100"), expires_at=None
    )

    n = await run_points_expire(session=db_session)
    assert n == 0

    await db_session.refresh(cust)
    assert cust.points_balance == Decimal("100")


async def test_job_is_idempotent(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Running the job twice on the same expired row writes exactly one
    reversal — the marker ``expired_source:<id>`` deduplicates."""
    yesterday = datetime.now(UTC) - timedelta(days=1)
    cust, _ = await _make_customer_with_earn(
        db_session, seed_tenant, earn=Decimal("75"), expires_at=yesterday
    )

    first = await run_points_expire(session=db_session)
    second = await run_points_expire(session=db_session)
    assert first == 1
    assert second == 0

    rows = (
        await db_session.execute(
            select(CustomerPointsLedger).where(
                CustomerPointsLedger.customer_id == cust.id,
                CustomerPointsLedger.reason == "expire.batch",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_balance_capped_when_already_redeemed_against_expired_points(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Customer earned 50, redeemed 40 → balance = 10. The 50-point earn
    expires. The job must NOT make balance negative — at most it pulls
    the remaining 10 down to 0."""
    yesterday = datetime.now(UTC) - timedelta(days=1)
    cust, _ = await _make_customer_with_earn(
        db_session,
        seed_tenant,
        earn=Decimal("50"),
        expires_at=yesterday,
        starting_balance=Decimal("10"),  # simulating already-redeemed 40
    )

    n = await run_points_expire(session=db_session)
    assert n == 1

    await db_session.refresh(cust)
    # Balance pulled from 10 → 0, NOT to -40.
    assert cust.points_balance == Decimal("0")


# ──────────────────────────────────────────────────────────────────────────
# orders_service.close stamps expires_at
# ──────────────────────────────────────────────────────────────────────────


async def test_order_close_stamps_expires_at_on_earn_row(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store,
    seed_menu_item,
) -> None:
    """Place an order linked to a customer, close it, then read the
    just-written earn row — expires_at must be set to
    closed_at + settings.points_expiry_days (365 by default)."""
    from restaurant_api.config import get_settings
    from restaurant_api.schemas.orders import OrderCreateRequest, OrderLineCreate
    from restaurant_api.services import orders_service

    cust = Customer(
        tenant_id=seed_tenant.id,
        display_name="exp",
        tier=CustomerTier.REGULAR,
    )
    db_session.add(cust)
    await db_session.flush()

    payload = OrderCreateRequest(
        store_id=seed_store.id,
        customer_id=cust.id,
        order_no=f"E-{uuid.uuid4().hex[:8]}",
        business_date=date.today(),
        opened_at=datetime.now(UTC),
        lines=[
            OrderLineCreate(
                menu_item_id=seed_menu_item.id,
                qty=Decimal("1"),
                unit_price=Decimal("1000.00"),
            )
        ],
    )
    response, _ = await orders_service.create_order(
        session=db_session, payload=payload, tenant_id=seed_tenant.id
    )
    closed_at = datetime.now(UTC)
    await orders_service.close_order(
        session=db_session,
        order_id=response.id,
        tenant_id=seed_tenant.id,
        closed_at=closed_at,
    )

    ledger = (
        await db_session.execute(
            select(CustomerPointsLedger).where(
                CustomerPointsLedger.customer_id == cust.id,
                CustomerPointsLedger.reason == "order.earn",
            )
        )
    ).scalar_one()

    expected_days = get_settings().points_expiry_days
    assert expected_days > 0  # default settings should set this
    assert ledger.expires_at is not None
    # Allow a few seconds of clock skew between closed_at and our assertion.
    diff = ledger.expires_at - closed_at
    assert abs(diff - timedelta(days=expected_days)) < timedelta(seconds=5)
