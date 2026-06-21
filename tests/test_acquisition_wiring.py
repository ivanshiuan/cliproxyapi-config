"""End-to-end wiring tests for the acquisition flywheel.

Unlike test_referral_service.py (which calls referral_service directly), these
drive the **real production call paths**:
  - ``campaigns_service.spin(ref=...)`` → referral attribution on register
  - ``orders_service.close_order(...)`` → referral qualification on first spend

This is the integration that the unit tests don't cover: that the ``ref`` code
actually flows through spin, and that closing a real order actually fires
qualification (referrer override points).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import (
    CampaignStatus,
    Customer,
    CustomerPointsLedger,
    MenuItem,
    Referral,
    ReferralStatus,
    Store,
    Tenant,
)
from restaurant_api.schemas.campaigns import CampaignCreate, PrizeCreate, SpinRequest
from restaurant_api.schemas.orders import OrderCreateRequest, OrderLineCreate
from restaurant_api.services import campaigns_service, orders_service, referral_service

pytestmark = pytest.mark.asyncio


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


async def _active_campaign(
    session: AsyncSession, *, tenant_id: uuid.UUID, store_id: uuid.UUID
) -> uuid.UUID:
    camp = await campaigns_service.create_campaign(
        session,
        CampaignCreate(
            name="開幕輪盤",
            slug=f"c-{uuid.uuid4().hex[:8]}",
            status=CampaignStatus.ACTIVE,
            daily_spin_limit=1,
            store_id=store_id,
        ),
        tenant_id=tenant_id,
    )
    await campaigns_service.add_prize(
        session,
        camp.id,
        PrizeCreate(name="銅板小菜", weight=1, value_estimate=Decimal("50")),
        tenant_id=tenant_id,
    )
    return camp.id


async def test_spin_with_ref_attributes_new_member(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    stub_messenger: StubLineMessenger,
) -> None:
    """A friend who spins the wheel with a ref code gets attributed + gifted."""
    referrer = Customer(tenant_id=seed_tenant.id, display_name="老客")
    db_session.add(referrer)
    await db_session.flush()
    code = await referral_service.ensure_referral_code(
        db_session, referrer, tenant_id=seed_tenant.id
    )

    campaign_id = await _active_campaign(
        db_session, tenant_id=seed_tenant.id, store_id=seed_store.id
    )

    # Brand-new member registers via the wheel, carrying the ref code.
    result = await campaigns_service.spin(
        db_session,
        campaign_id,
        SpinRequest(line_user_id=f"U{uuid.uuid4().hex[:16]}", ref=code),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )
    assert result.is_new_member is True
    friend_id = result.customer_id

    # A pending referral edge now exists and the friend got the signup gift.
    edge = (
        await db_session.execute(
            select(Referral).where(Referral.referred_id == friend_id)
        )
    ).scalar_one_or_none()
    assert edge is not None
    assert edge.referrer_id == referrer.id
    assert edge.status == ReferralStatus.PENDING
    assert (
        await _points(db_session, friend_id, "referral.signup")
        == referral_service.REFERRED_SIGNUP_POINTS
    )


async def test_close_order_qualifies_referral_and_pays_referrer(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
    stub_messenger: StubLineMessenger,
) -> None:
    """Closing the referred member's first real order fires qualification."""
    referrer = Customer(
        tenant_id=seed_tenant.id,
        display_name="老客",
        line_user_id=f"U{uuid.uuid4().hex[:16]}",
    )
    friend = Customer(tenant_id=seed_tenant.id, display_name="新朋友")
    db_session.add_all([referrer, friend])
    await db_session.flush()
    code = await referral_service.ensure_referral_code(
        db_session, referrer, tenant_id=seed_tenant.id
    )
    await referral_service.attribute_referral(
        db_session, referred=friend, code=code, tenant_id=seed_tenant.id
    )

    # Real order lifecycle: create → close.
    created, _ = await orders_service.create_order(
        db_session,
        OrderCreateRequest(
            store_id=seed_store.id,
            customer_id=friend.id,
            order_no=f"O{uuid.uuid4().hex[:10]}",
            business_date=date(2026, 6, 19),
            lines=[
                OrderLineCreate(
                    menu_item_id=seed_menu_item.id,
                    qty=Decimal("1"),
                    unit_price=Decimal("300"),
                )
            ],
        ),
        tenant_id=seed_tenant.id,
        messenger=stub_messenger,
    )
    await orders_service.close_order(
        db_session,
        created.id,
        seed_tenant.id,
        datetime.now(UTC),
        messenger=stub_messenger,
    )

    # Referral flipped to qualified and the referrer earned override points.
    edge = (
        await db_session.execute(
            select(Referral).where(Referral.referred_id == friend.id)
        )
    ).scalar_one()
    assert edge.status == ReferralStatus.QUALIFIED
    assert edge.qualifying_order_id == created.id
    assert (
        await _points(db_session, referrer.id, "referral.bonus")
        == referral_service.REFERRER_REWARD_POINTS
    )
    # Referrer got the "friend came back" LINE nudge.
    assert any(
        m.get("to") == referrer.line_user_id  # type: ignore[union-attr]
        for m in stub_messenger.sent_messages
    )
