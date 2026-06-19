"""裂變飛輪 — referral service tests (scoped to seed_tenant).

Covers the full circle: code minting, signup attribution + welcome gift,
first-spend qualification + referrer override points + LINE, and the guards
(unknown code, self-referral, referred-once, qualify-once).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import (
    Customer,
    CustomerPointsLedger,
    Order,
    OrderStatus,
    ReferralStatus,
)
from restaurant_api.services import referral_service

pytestmark = pytest.mark.asyncio


async def _customer(session: AsyncSession, tenant_id: uuid.UUID, **kw) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="客", **kw)
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


async def _points(session: AsyncSession, customer_id: uuid.UUID, reason: str) -> Decimal:
    return Decimal(
        (
            await session.execute(
                select(func.coalesce(func.sum(CustomerPointsLedger.delta), 0)).where(
                    CustomerPointsLedger.customer_id == customer_id,
                    CustomerPointsLedger.reason == reason,
                )
            )
        ).scalar_one()
    )


# ── code minting ─────────────────────────────────────────────────────────────


async def test_code_is_minted_once_and_stable(
    db_session: AsyncSession, seed_tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    code1 = await referral_service.ensure_referral_code(
        db_session, cust, tenant_id=seed_tenant.id
    )
    assert len(code1) == 8
    code2 = await referral_service.ensure_referral_code(
        db_session, cust, tenant_id=seed_tenant.id
    )
    assert code1 == code2  # stable


# ── attribution ──────────────────────────────────────────────────────────────


async def test_attribution_creates_edge_and_gifts_referred(
    db_session: AsyncSession, seed_tenant
) -> None:
    referrer = await _customer(db_session, seed_tenant.id)
    code = await referral_service.ensure_referral_code(
        db_session, referrer, tenant_id=seed_tenant.id
    )
    friend = await _customer(db_session, seed_tenant.id)

    ref = await referral_service.attribute_referral(
        db_session, referred=friend, code=code, tenant_id=seed_tenant.id
    )
    assert ref is not None
    assert ref.status == ReferralStatus.PENDING
    assert (
        await _points(db_session, friend.id, "referral.signup")
        == referral_service.REFERRED_SIGNUP_POINTS
    )


async def test_attribution_ignores_unknown_code(
    db_session: AsyncSession, seed_tenant
) -> None:
    friend = await _customer(db_session, seed_tenant.id)
    ref = await referral_service.attribute_referral(
        db_session, referred=friend, code="ZZZZZZZZ", tenant_id=seed_tenant.id
    )
    assert ref is None


async def test_attribution_ignores_self_referral(
    db_session: AsyncSession, seed_tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    code = await referral_service.ensure_referral_code(
        db_session, cust, tenant_id=seed_tenant.id
    )
    ref = await referral_service.attribute_referral(
        db_session, referred=cust, code=code, tenant_id=seed_tenant.id
    )
    assert ref is None


async def test_member_referred_only_once(
    db_session: AsyncSession, seed_tenant
) -> None:
    r1 = await _customer(db_session, seed_tenant.id)
    r2 = await _customer(db_session, seed_tenant.id)
    c1 = await referral_service.ensure_referral_code(
        db_session, r1, tenant_id=seed_tenant.id
    )
    c2 = await referral_service.ensure_referral_code(
        db_session, r2, tenant_id=seed_tenant.id
    )
    friend = await _customer(db_session, seed_tenant.id)

    first = await referral_service.attribute_referral(
        db_session, referred=friend, code=c1, tenant_id=seed_tenant.id
    )
    second = await referral_service.attribute_referral(
        db_session, referred=friend, code=c2, tenant_id=seed_tenant.id
    )
    assert first is not None
    assert second is None  # already referred


# ── qualification ────────────────────────────────────────────────────────────


async def test_qualify_grants_referrer_and_pushes(
    db_session: AsyncSession, seed_tenant, seed_store, stub_messenger: StubLineMessenger
) -> None:
    referrer = await _customer(
        db_session, seed_tenant.id, line_user_id=f"U{uuid.uuid4().hex[:16]}"
    )
    code = await referral_service.ensure_referral_code(
        db_session, referrer, tenant_id=seed_tenant.id
    )
    friend = await _customer(db_session, seed_tenant.id)
    await referral_service.attribute_referral(
        db_session, referred=friend, code=code, tenant_id=seed_tenant.id
    )

    order = await _bare_order(
        db_session, tenant_id=seed_tenant.id, store_id=seed_store.id
    )
    ref = await referral_service.qualify_referral(
        db_session,
        referred_id=friend.id,
        tenant_id=seed_tenant.id,
        order_id=order.id,
        messenger=stub_messenger,
    )
    assert ref is not None
    assert ref.status == ReferralStatus.QUALIFIED
    assert (
        await _points(db_session, referrer.id, "referral.bonus")
        == referral_service.REFERRER_REWARD_POINTS
    )
    assert any(
        m.get("to") == referrer.line_user_id  # type: ignore[union-attr]
        for m in stub_messenger.sent_messages
    )

    # Second qualify is a no-op (one-shot).
    again = await referral_service.qualify_referral(
        db_session,
        referred_id=friend.id,
        tenant_id=seed_tenant.id,
        order_id=uuid.uuid4(),
        messenger=stub_messenger,
    )
    assert again is None
    assert (
        await _points(db_session, referrer.id, "referral.bonus")
        == referral_service.REFERRER_REWARD_POINTS
    )


async def test_qualify_without_referral_is_noop(
    db_session: AsyncSession, seed_tenant
) -> None:
    lone = await _customer(db_session, seed_tenant.id)
    ref = await referral_service.qualify_referral(
        db_session,
        referred_id=lone.id,
        tenant_id=seed_tenant.id,
        order_id=uuid.uuid4(),
    )
    assert ref is None
