"""送券裂變 — 模式 A voucher_gift_service tests (scoped to seed_tenant).

The point of this file is the anti-fraud contract (docs/18 §4):
- reward fires at redemption, never at share/claim;
- a sender can't claim their own gift (self-send farming);
- a gift is rewarded at most once (one-shot);
- a sender's monthly reward is capped.
Plus the happy path end-to-end.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.errors import ConflictError, NotFoundError
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.models import (
    Customer,
    CustomerPointsLedger,
    Tenant,
    VoucherGiftStatus,
    VoucherRewardKind,
)
from restaurant_api.services import voucher_gift_service as svc

pytestmark = pytest.mark.asyncio


async def _customer(session: AsyncSession, tenant_id: uuid.UUID, **kw) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="客", **kw)
    session.add(cust)
    await session.flush()
    return cust


async def _points(session: AsyncSession, customer_id: uuid.UUID) -> Decimal:
    return Decimal(
        (
            await session.execute(
                select(func.coalesce(func.sum(CustomerPointsLedger.delta), 0)).where(
                    CustomerPointsLedger.customer_id == customer_id,
                    CustomerPointsLedger.reason == "voucher_gift.reward",
                )
            )
        ).scalar_one()
    )


# ── create ───────────────────────────────────────────────────────────────────


async def test_create_gift_starts_pending_with_default_reward(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    assert gift.status is VoucherGiftStatus.PENDING
    assert gift.reward_kind is VoucherRewardKind.POINTS
    assert gift.reward_amount == svc.GIFT_REWARD_POINTS
    assert len(gift.share_token) == 20
    assert gift.recipient_id is None


async def test_create_gift_unknown_sender_404(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        await svc.create_gift(
            db_session, sender_id=uuid.uuid4(), tenant_id=seed_tenant.id
        )


# ── claim + anti-fraud ────────────────────────────────────────────────────────


async def test_claim_binds_recipient(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    friend = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    claimed = await svc.claim_gift(
        db_session, share_token=gift.share_token, recipient_id=friend.id, tenant_id=seed_tenant.id
    )
    assert claimed.status is VoucherGiftStatus.CLAIMED
    assert claimed.recipient_id == friend.id


async def test_cannot_claim_own_gift(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """Anti-fraud: self-send-and-claim to farm the reward is rejected."""
    sender = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    with pytest.raises(ConflictError):
        await svc.claim_gift(
            db_session,
            share_token=gift.share_token,
            recipient_id=sender.id,
            tenant_id=seed_tenant.id,
        )


async def test_cannot_claim_twice(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    f1 = await _customer(db_session, seed_tenant.id)
    f2 = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    await svc.claim_gift(
        db_session, share_token=gift.share_token, recipient_id=f1.id, tenant_id=seed_tenant.id
    )
    with pytest.raises(ConflictError):
        await svc.claim_gift(
            db_session, share_token=gift.share_token, recipient_id=f2.id, tenant_id=seed_tenant.id
        )


async def test_claim_unknown_token_404(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    friend = await _customer(db_session, seed_tenant.id)
    with pytest.raises(NotFoundError):
        await svc.claim_gift(
            db_session, share_token="nope", recipient_id=friend.id, tenant_id=seed_tenant.id
        )


# ── redeem: reward-on-redemption, one-shot ────────────────────────────────────


async def test_redeem_grants_sender_reward(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    stub = StubLineMessenger()
    sender = await _customer(
        db_session, seed_tenant.id, line_user_id=f"U{uuid.uuid4().hex[:16]}"
    )
    friend = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    await svc.claim_gift(
        db_session, share_token=gift.share_token, recipient_id=friend.id, tenant_id=seed_tenant.id
    )
    # Reward NOT yet paid — only claimed, not redeemed.
    assert await _points(db_session, sender.id) == Decimal("0")

    redeemed = await svc.redeem_gift(
        db_session, gift_id=gift.id, tenant_id=seed_tenant.id, messenger=stub
    )
    assert redeemed.status is VoucherGiftStatus.REDEEMED
    assert redeemed.rewarded_at is not None
    assert await _points(db_session, sender.id) == svc.GIFT_REWARD_POINTS
    # sender got a LINE nudge
    assert any(m["op"] == "push" for m in stub.sent_messages)


async def test_redeem_is_one_shot(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """Anti-fraud: the sender bonus can never be double-granted."""
    sender = await _customer(db_session, seed_tenant.id)
    friend = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    await svc.claim_gift(
        db_session, share_token=gift.share_token, recipient_id=friend.id, tenant_id=seed_tenant.id
    )
    await svc.redeem_gift(db_session, gift_id=gift.id, tenant_id=seed_tenant.id)
    with pytest.raises(ConflictError):
        await svc.redeem_gift(db_session, gift_id=gift.id, tenant_id=seed_tenant.id)
    # Still exactly one reward on the ledger.
    assert await _points(db_session, sender.id) == svc.GIFT_REWARD_POINTS


async def test_redeem_succeeds_even_if_sender_deleted(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """The friend's redemption must not be blocked by a deleted sender; the
    sender simply forfeits the reward."""
    from datetime import UTC, datetime

    sender = await _customer(db_session, seed_tenant.id)
    friend = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    await svc.claim_gift(
        db_session, share_token=gift.share_token, recipient_id=friend.id, tenant_id=seed_tenant.id
    )
    # sender leaves / is erased after claiming
    sender.deleted_at = datetime.now(UTC)
    await db_session.flush()

    redeemed = await svc.redeem_gift(
        db_session, gift_id=gift.id, tenant_id=seed_tenant.id
    )
    assert redeemed.status is VoucherGiftStatus.REDEEMED  # redemption succeeded
    assert await _points(db_session, sender.id) == Decimal("0")  # no reward paid


async def test_cannot_redeem_unclaimed_gift(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    gift = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    with pytest.raises(ConflictError):
        await svc.redeem_gift(db_session, gift_id=gift.id, tenant_id=seed_tenant.id)


# ── monthly cap ───────────────────────────────────────────────────────────────


async def test_monthly_reward_cap_clamps_payout(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    """Anti-fraud: a sender's monthly reward can't exceed the cap.

    Fill most of the cap with one big-reward gift, then a normal gift is clamped
    to just the remaining headroom.
    """
    sender = await _customer(db_session, seed_tenant.id)
    near_cap = svc.MONTHLY_REWARD_CAP_POINTS - Decimal("50")

    f1 = await _customer(db_session, seed_tenant.id)
    g1 = await svc.create_gift(
        db_session,
        sender_id=sender.id,
        tenant_id=seed_tenant.id,
        reward_amount=near_cap,
    )
    await svc.claim_gift(
        db_session, share_token=g1.share_token, recipient_id=f1.id, tenant_id=seed_tenant.id
    )
    await svc.redeem_gift(db_session, gift_id=g1.id, tenant_id=seed_tenant.id)

    # Second gift's default 100-point reward should clamp to the remaining 50.
    f2 = await _customer(db_session, seed_tenant.id)
    g2 = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    await svc.claim_gift(
        db_session, share_token=g2.share_token, recipient_id=f2.id, tenant_id=seed_tenant.id
    )
    redeemed = await svc.redeem_gift(
        db_session, gift_id=g2.id, tenant_id=seed_tenant.id
    )
    assert redeemed.reward_amount == Decimal("50")
    assert await _points(db_session, sender.id) == svc.MONTHLY_REWARD_CAP_POINTS


async def test_cap_exhausted_pays_zero(
    db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    f1 = await _customer(db_session, seed_tenant.id)
    g1 = await svc.create_gift(
        db_session,
        sender_id=sender.id,
        tenant_id=seed_tenant.id,
        reward_amount=svc.MONTHLY_REWARD_CAP_POINTS,
    )
    await svc.claim_gift(
        db_session, share_token=g1.share_token, recipient_id=f1.id, tenant_id=seed_tenant.id
    )
    await svc.redeem_gift(db_session, gift_id=g1.id, tenant_id=seed_tenant.id)

    f2 = await _customer(db_session, seed_tenant.id)
    g2 = await svc.create_gift(
        db_session, sender_id=sender.id, tenant_id=seed_tenant.id
    )
    await svc.claim_gift(
        db_session, share_token=g2.share_token, recipient_id=f2.id, tenant_id=seed_tenant.id
    )
    redeemed = await svc.redeem_gift(
        db_session, gift_id=g2.id, tenant_id=seed_tenant.id
    )
    assert redeemed.reward_amount == Decimal("0")
    assert await _points(db_session, sender.id) == svc.MONTHLY_REWARD_CAP_POINTS
