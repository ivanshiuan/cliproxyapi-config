"""成效報表 — membership stats aggregation tests.

Drives all four growth systems with real services, then asserts the
``compute_stats`` snapshot adds up.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import (
    Customer,
    CustomerTier,
    Order,
    OrderStatus,
    Store,
    Tenant,
    UgcKind,
)
from restaurant_api.schemas.stored_value import StoredValueTopUpRequest
from restaurant_api.schemas.ugc import UgcReviewRequest, UgcSubmitRequest
from restaurant_api.services import (
    membership_stats_service,
    referral_service,
    stored_value_service,
    ugc_service,
)

pytestmark = pytest.mark.asyncio


async def _customer(
    session: AsyncSession, tenant_id: uuid.UUID, *, tier: CustomerTier, **kw
) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="客", tier=tier, **kw)
    session.add(cust)
    await session.flush()
    return cust


async def _bare_order(
    session: AsyncSession, *, tenant_id: uuid.UUID, store_id: uuid.UUID
) -> Order:
    now = datetime.now(UTC)
    order = Order(
        tenant_id=tenant_id,
        store_id=store_id,
        order_no=f"R{uuid.uuid4().hex[:10]}",
        business_date=now.date(),
        opened_at=now,
        closed_at=now,
        status=OrderStatus.CLOSED,
    )
    session.add(order)
    await session.flush()
    return order


async def test_stats_aggregate_across_all_systems(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    stub_messenger: StubLineMessenger,
) -> None:
    tid = seed_tenant.id

    # 4 members across tiers (REGULARx2, GOLD, SILVER).
    referrer = await _customer(
        db_session, tid, tier=CustomerTier.REGULAR, line_user_id=f"U{uuid.uuid4().hex[:16]}"
    )
    friend = await _customer(db_session, tid, tier=CustomerTier.REGULAR)
    gold = await _customer(db_session, tid, tier=CustomerTier.GOLD)
    await _customer(db_session, tid, tier=CustomerTier.SILVER)

    # 儲值: referrer tops up 1000 (+200 bonus) → balance 1200.
    await stored_value_service.top_up(
        db_session,
        referrer.id,
        StoredValueTopUpRequest(amount=Decimal("1000")),
        tenant_id=tid,
        messenger=stub_messenger,
    )

    # 裂變: friend referred by referrer, then qualifies on first order.
    code = await referral_service.ensure_referral_code(db_session, referrer, tenant_id=tid)
    await referral_service.attribute_referral(
        db_session, referred=friend, code=code, tenant_id=tid
    )  # friend +100 (referral.signup)
    order = await _bare_order(db_session, tenant_id=tid, store_id=seed_store.id)
    await referral_service.qualify_referral(
        db_session, referred_id=friend.id, tenant_id=tid, order_id=order.id,
        messenger=stub_messenger,
    )  # referrer +200 (referral.bonus)

    # UGC: gold submits a google review (approved +100) and an IG post (rejected).
    g = await ugc_service.submit(
        db_session, UgcSubmitRequest(customer_id=gold.id, kind=UgcKind.GOOGLE_REVIEW),
        tenant_id=tid,
    )
    await ugc_service.approve(
        db_session, g.id, UgcReviewRequest(), tenant_id=tid, messenger=stub_messenger
    )
    i = await ugc_service.submit(
        db_session, UgcSubmitRequest(customer_id=gold.id, kind=UgcKind.IG_POST),
        tenant_id=tid,
    )
    await ugc_service.reject(db_session, i.id, UgcReviewRequest(note="不清楚"), tenant_id=tid)

    stats = await membership_stats_service.compute_stats(db_session, tenant_id=tid)

    # ── members + tiers ──
    assert stats.total_members == 4
    by_tier = {b.tier: b.count for b in stats.tiers}
    assert by_tier[CustomerTier.REGULAR] == 2
    assert by_tier[CustomerTier.GOLD] == 1
    assert by_tier[CustomerTier.SILVER] == 1

    # ── points: 100 (signup) + 200 (bonus) + 100 (ugc) = 400 granted, 0 redeemed ──
    assert stats.points.lifetime_granted == Decimal("400")
    assert stats.points.lifetime_redeemed == Decimal("0")
    assert stats.points.outstanding_balance == Decimal("400")

    # ── stored value ──
    assert stats.stored_value.outstanding_balance == Decimal("1200")
    assert stats.stored_value.members_with_balance == 1
    assert stats.stored_value.penetration_pct == Decimal("25.00")  # 1/4
    assert stats.stored_value.lifetime_topup == Decimal("1000")
    assert stats.stored_value.lifetime_bonus == Decimal("200")

    # ── referral ──
    assert stats.referral.total == 1
    assert stats.referral.qualified == 1
    assert stats.referral.pending == 0
    assert stats.referral.distinct_referrers == 1
    assert stats.referral.conversion_pct == Decimal("100.00")
    assert stats.referral.k_factor == Decimal("0.250")  # 1/4

    # ── ugc ──
    assert stats.ugc.approved == 1
    assert stats.ugc.rejected == 1
    assert stats.ugc.pending == 0
    assert stats.ugc.approval_pct == Decimal("50.00")
    kinds = {b.kind: b.count for b in stats.ugc.by_kind}
    assert kinds[UgcKind.GOOGLE_REVIEW] == 1
    assert kinds[UgcKind.IG_POST] == 1


async def test_stats_empty_tenant_is_all_zero(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """No members / activity → safe zeros, no divide-by-zero."""
    stats = await membership_stats_service.compute_stats(
        db_session, tenant_id=seed_tenant.id
    )
    assert stats.window_days is None
    assert stats.total_members == 0
    assert stats.stored_value.penetration_pct == Decimal("0")
    assert stats.referral.conversion_pct == Decimal("0")
    assert stats.referral.k_factor == Decimal("0")
    assert stats.ugc.approval_pct == Decimal("0")


async def test_stats_time_window_filters_flow_not_snapshot(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """?days=N windows ledger flow but leaves the balance snapshot intact."""
    from datetime import UTC, datetime, timedelta

    from restaurant_api.models import CustomerPointsLedger

    tid = seed_tenant.id
    cust = await _customer(db_session, tid, tier=CustomerTier.REGULAR)
    cust.points_balance = Decimal("300")  # snapshot liability
    now = datetime.now(UTC)

    # One old grant (40d ago) + one recent grant (today).
    db_session.add_all(
        [
            CustomerPointsLedger(
                tenant_id=tid, customer_id=cust.id, delta=Decimal("100"),
                reason="order.earn", created_at=now - timedelta(days=40),
            ),
            CustomerPointsLedger(
                tenant_id=tid, customer_id=cust.id, delta=Decimal("200"),
                reason="order.earn", created_at=now,
            ),
        ]
    )
    await db_session.flush()

    # Lifetime: both grants count.
    lifetime = await membership_stats_service.compute_stats(db_session, tenant_id=tid)
    assert lifetime.window_days is None
    assert lifetime.points.lifetime_granted == Decimal("300")

    # Last 30 days: only the recent grant; snapshot balance unchanged.
    windowed = await membership_stats_service.compute_stats(
        db_session, tenant_id=tid, since=now - timedelta(days=30), window_days=30
    )
    assert windowed.window_days == 30
    assert windowed.points.lifetime_granted == Decimal("200")
    assert windowed.points.outstanding_balance == Decimal("300")  # snapshot intact
