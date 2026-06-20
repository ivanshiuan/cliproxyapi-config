"""RFM 分眾 — classification + segment counts + segment broadcast tests."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import Customer, Order, OrderStatus, Store, Tenant
from restaurant_api.schemas.rfm import RfmSegment
from restaurant_api.services import rfm_service

pytestmark = pytest.mark.asyncio

_AS_OF = date(2026, 6, 19)


async def _member(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    lifetime_value: Decimal = Decimal("0"),
    line: bool = False,
) -> Customer:
    cust = Customer(
        tenant_id=tenant_id,
        display_name="客",
        lifetime_value=lifetime_value,
        line_user_id=f"U{uuid.uuid4().hex[:16]}" if line else None,
    )
    session.add(cust)
    await session.flush()
    return cust


async def _orders_on(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    customer_id: uuid.UUID,
    days: list[date],
) -> None:
    for d in days:
        session.add(
            Order(
                tenant_id=tenant_id,
                store_id=store_id,
                customer_id=customer_id,
                order_no=f"F{uuid.uuid4().hex[:10]}",
                business_date=d,
                opened_at=datetime.now(UTC),
                closed_at=datetime.now(UTC),
                status=OrderStatus.CLOSED,
            )
        )
    await session.flush()


# ── pure classification ──────────────────────────────────────────────────────


def test_classify_decision_tree() -> None:
    c = rfm_service.classify
    assert c(recency_days=None, frequency=0, monetary=Decimal("0")) == RfmSegment.NEW
    # gone quiet but high value -> at risk; low value -> dormant
    assert c(recency_days=60, frequency=5, monetary=Decimal("0")) == RfmSegment.AT_RISK
    assert c(recency_days=60, frequency=1, monetary=Decimal("0")) == RfmSegment.DORMANT
    # recent, very frequent + high spend -> champion
    assert c(recency_days=3, frequency=12, monetary=Decimal("20000")) == RfmSegment.CHAMPION
    # recent + moderately frequent -> loyal
    assert c(recency_days=10, frequency=4, monetary=Decimal("0")) == RfmSegment.LOYAL
    # recent single visit -> potential
    assert c(recency_days=10, frequency=1, monetary=Decimal("0")) == RfmSegment.POTENTIAL


# ── segment counts ───────────────────────────────────────────────────────────


async def test_segment_counts_cover_all_labels(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    tid, sid = seed_tenant.id, seed_store.id

    # NEW: registered, no orders.
    await _member(db_session, tid)

    # CHAMPION: recent, 12 orders, high lifetime value.
    champ = await _member(db_session, tid, lifetime_value=Decimal("20000"))
    await _orders_on(
        db_session, tenant_id=tid, store_id=sid, customer_id=champ.id,
        days=[_AS_OF - timedelta(days=k) for k in range(12)],
    )

    # AT_RISK: high frequency but last visit 60 days ago.
    risk = await _member(db_session, tid, lifetime_value=Decimal("5000"))
    await _orders_on(
        db_session, tenant_id=tid, store_id=sid, customer_id=risk.id,
        days=[_AS_OF - timedelta(days=60 + k) for k in range(5)],
    )

    res = await rfm_service.segment_counts(db_session, tenant_id=tid, as_of=_AS_OF)

    assert res.total_scored == 3
    counts = {b.segment: b.count for b in res.segments}
    # every label present in the response (0 when empty)
    assert set(counts) == set(RfmSegment)
    assert counts[RfmSegment.NEW] == 1
    assert counts[RfmSegment.CHAMPION] == 1
    assert counts[RfmSegment.AT_RISK] == 1


# ── segment broadcast ────────────────────────────────────────────────────────


async def test_broadcast_targets_segment_members_with_line(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    stub_messenger: StubLineMessenger,
) -> None:
    tid, sid = seed_tenant.id, seed_store.id

    # Two AT_RISK members, only one reachable on LINE.
    for has_line in (True, False):
        m = await _member(db_session, tid, lifetime_value=Decimal("5000"), line=has_line)
        await _orders_on(
            db_session, tenant_id=tid, store_id=sid, customer_id=m.id,
            days=[_AS_OF - timedelta(days=60 + k) for k in range(5)],
        )

    result = await rfm_service.broadcast_to_segment(
        db_session,
        tenant_id=tid,
        segment=RfmSegment.AT_RISK,
        message="想念您! 回來享 9 折 🎁",
        as_of=_AS_OF,
        messenger=stub_messenger,
    )

    assert result.segment == RfmSegment.AT_RISK
    assert result.audience_size == 1  # only the LINE-reachable member
    assert result.delivered == 1
    assert stub_messenger.sent_messages[-1]["op"] == "broadcast"


async def test_broadcast_empty_segment_delivers_zero(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    stub_messenger: StubLineMessenger,
) -> None:
    result = await rfm_service.broadcast_to_segment(
        db_session,
        tenant_id=seed_tenant.id,
        segment=RfmSegment.CHAMPION,
        message="VIP 專屬",
        as_of=_AS_OF,
        messenger=stub_messenger,
    )
    assert result.audience_size == 0
    assert result.delivered == 0
