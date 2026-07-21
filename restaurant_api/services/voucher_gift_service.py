"""送券裂變 — 模式 A member-to-friend voucher gifting service.

The viral loop the operator asked for: a member shares a voucher via a tokenised
link → a friend claims it (加好友 → bound as recipient) → the friend comes in and
the counter redeems it → **only then** the sender earns a reward. Points accrue;
the member spends them on merchandise, and shares again.

Why the reward fires at *redemption*, never at *share*
------------------------------------------------------
"每發一張券就給 $10" paid at share time is a fraud faucet — a bad actor rings up
fake friends and farms the payout with zero real guests. Binding the reward to
"the friend actually redeemed at the counter" makes the payout cost land only
when a real guest eats. This mirrors ``referral_service.qualify_referral``
(reward-on-first-spend), the anti-farming pattern already proven here.

Anti-fraud, three locks (see ``docs/18_voucher_sharing_design.md`` §4):
1. Reward only at redemption (this module's ``redeem_gift``), never at share.
2. A recipient cannot be the sender — self-send-and-claim is rejected.
3. A per-member monthly reward ceiling caps extreme farming behaviour.

Correctness:
- ``rewarded_at`` is the one-shot guard: a gift is rewarded at most once, even
  under concurrent redemption attempts (the row is locked ``FOR UPDATE``).
- The sender row is locked ``FOR UPDATE`` before its cached ``points_balance`` is
  bumped, so two friends redeeming this sender's gifts concurrently can't clobber
  each other's cached-balance write (the ledger stays correct regardless).
- ``flush()`` only; the caller owns the transaction.
"""

from __future__ import annotations

import contextlib
import secrets
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..integrations.line import LineMessage, LineMessenger, get_messenger
from ..models import (
    Customer,
    CustomerPointsLedger,
    VoucherGift,
    VoucherGiftStatus,
    VoucherRewardKind,
)
from .audit_service import audit

# Tunable defaults (docs/18 §5). Move to tenant_settings per-brand later — same
# module-constant shape as referral_service's reward knobs.
GIFT_REWARD_POINTS = Decimal("100")  # 送券成功回饋(朋友核銷才發)
MONTHLY_REWARD_CAP_POINTS = Decimal("5000")  # 每會員每月裂變回饋上限(防農場)

_TOKEN_LEN = 20  # url-safe token; 20 chars of the alphabet below = ~103 bits
_TOKEN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
_TOKEN_MAX_TRIES = 6


def _new_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LEN))


async def _load_customer(
    session: AsyncSession, customer_id: uuid.UUID, tenant_id: uuid.UUID
) -> Customer:
    """Load a live (non-deleted) customer, scoped to tenant, or raise 404."""
    row = (
        await session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        raise NotFoundError(
            message=f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    return row


async def _grant_points(
    session: AsyncSession,
    customer: Customer,
    *,
    delta: Decimal,
    reason: str,
    note: str | None = None,
) -> None:
    """Append a points-ledger row and bump the cached balance."""
    session.add(
        CustomerPointsLedger(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            delta=delta,
            reason=reason,
            note=note,
        )
    )
    customer.points_balance = (customer.points_balance or Decimal("0")) + delta


async def _rewarded_this_month(
    session: AsyncSession, *, sender_id: uuid.UUID, tenant_id: uuid.UUID, as_of: date
) -> Decimal:
    """Total gift reward already settled to this sender in ``as_of``'s month."""
    month_start = as_of.replace(day=1)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(VoucherGift.reward_amount), 0)).where(
                VoucherGift.tenant_id == tenant_id,
                VoucherGift.sender_id == sender_id,
                VoucherGift.rewarded_at.is_not(None),
                func.date(func.timezone("Asia/Taipei", VoucherGift.rewarded_at))
                >= month_start,
            )
        )
    ).scalar_one()
    return Decimal(str(total))


async def create_gift(
    session: AsyncSession,
    *,
    sender_id: uuid.UUID,
    tenant_id: uuid.UUID,
    reward_kind: VoucherRewardKind = VoucherRewardKind.POINTS,
    reward_amount: Decimal | None = None,
    actor_id: uuid.UUID | None = None,
) -> VoucherGift:
    """Mint a shareable gift link for ``sender``; the gift starts ``pending``."""
    sender = await _load_customer(session, sender_id, tenant_id)
    amount = GIFT_REWARD_POINTS if reward_amount is None else reward_amount

    gift: VoucherGift | None = None
    for _ in range(_TOKEN_MAX_TRIES):
        candidate = _new_token()
        try:
            async with session.begin_nested():
                gift = VoucherGift(
                    tenant_id=tenant_id,
                    sender_id=sender.id,
                    share_token=candidate,
                    status=VoucherGiftStatus.PENDING,
                    reward_kind=reward_kind,
                    reward_amount=amount,
                )
                session.add(gift)
                await session.flush()
            break
        except IntegrityError:
            gift = None  # token collided; retry
    if gift is None:
        raise RuntimeError("could not allocate a unique gift token")

    await audit(
        session,
        action="voucher_gift.created",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("voucher_gifts", gift.id),
        after={
            "sender_id": str(sender.id),
            "reward_kind": reward_kind.value,
            "reward_amount": str(amount),
        },
    )
    return gift


async def claim_gift(
    session: AsyncSession,
    *,
    share_token: str,
    recipient_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> VoucherGift:
    """A friend claims a pending gift, binding themselves as the recipient.

    Anti-fraud: the recipient cannot be the sender (self-send-and-claim), and a
    gift can only be claimed once (guarded by the ``pending`` status under lock).
    """
    recipient = await _load_customer(session, recipient_id, tenant_id)
    gift = (
        await session.execute(
            select(VoucherGift)
            .where(
                VoucherGift.tenant_id == tenant_id,
                VoucherGift.share_token == share_token,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if gift is None:
        raise NotFoundError(
            message="gift link not found",
            details={"share_token": share_token},
        )
    if gift.status != VoucherGiftStatus.PENDING:
        raise ConflictError(
            message="this gift has already been claimed or is no longer active",
            details={"status": gift.status.value},
        )
    if gift.sender_id == recipient.id:
        raise ConflictError(
            message="cannot claim your own gift",
            details={"gift_id": str(gift.id)},
        )

    gift.recipient_id = recipient.id
    gift.status = VoucherGiftStatus.CLAIMED
    await session.flush()
    await audit(
        session,
        action="voucher_gift.claimed",
        tenant_id=tenant_id,
        target=("voucher_gifts", gift.id),
        after={"recipient_id": str(recipient.id)},
    )
    return gift


async def redeem_gift(
    session: AsyncSession,
    *,
    gift_id: uuid.UUID,
    tenant_id: uuid.UUID,
    as_of: date | None = None,
    messenger: LineMessenger | None = None,
) -> VoucherGift:
    """Redeem a claimed gift at the counter and settle the sender's reward.

    One-shot: guarded by ``rewarded_at`` under a row lock, so the sender bonus can
    never be double-granted. The reward is clamped so the sender's running monthly
    total never exceeds ``MONTHLY_REWARD_CAP_POINTS`` (extreme-farming backstop).
    """
    today = as_of or datetime.now(UTC).date()
    gift = (
        await session.execute(
            select(VoucherGift)
            .where(VoucherGift.id == gift_id, VoucherGift.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if gift is None:
        raise NotFoundError(
            message=f"gift {gift_id} not found",
            details={"gift_id": str(gift_id)},
        )
    if gift.rewarded_at is not None:
        raise ConflictError(
            message="this gift has already been redeemed",
            details={"gift_id": str(gift_id)},
        )
    if gift.status != VoucherGiftStatus.CLAIMED:
        raise ConflictError(
            message="only a claimed gift can be redeemed",
            details={"status": gift.status.value},
        )

    # Clamp the reward against this sender's remaining monthly headroom.
    intended = gift.reward_amount
    already = await _rewarded_this_month(
        session, sender_id=gift.sender_id, tenant_id=tenant_id, as_of=today
    )
    remaining = MONTHLY_REWARD_CAP_POINTS - already
    payout = min(intended, max(Decimal("0"), remaining))
    was_capped = payout < intended

    gift.status = VoucherGiftStatus.REDEEMED
    gift.rewarded_at = datetime.now(UTC)
    # Record the reward we actually paid (may be clamped by the monthly cap).
    gift.reward_amount = payout

    # The friend's redemption must succeed even if the *sender* is gone — a
    # soft-deleted sender just forfeits the reward (mirrors referral_service's
    # deleted-referrer handling). Load leniently; only grant if the sender is
    # live. Locked FOR UPDATE to serialize the cached-balance bump.
    sender = (
        await session.execute(
            select(Customer)
            .where(
                Customer.id == gift.sender_id,
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if sender is not None and payout > 0 and gift.reward_kind == VoucherRewardKind.POINTS:
        await _grant_points(
            session,
            sender,
            delta=payout,
            reason="voucher_gift.reward",
            note=f"gift {gift.id} redeemed",
        )
    await session.flush()
    await audit(
        session,
        action="voucher_gift.redeemed",
        tenant_id=tenant_id,
        target=("voucher_gifts", gift.id),
        after={
            "sender_id": str(gift.sender_id),
            "recipient_id": str(gift.recipient_id) if gift.recipient_id else None,
            "reward_kind": gift.reward_kind.value,
            "reward_paid": str(payout),
            "monthly_capped": was_capped,
        },
    )

    out = messenger or get_messenger()
    if sender is not None and payout > 0 and sender.line_user_id:
        with contextlib.suppress(Exception):  # best-effort side channel
            await out.push(
                sender.line_user_id,
                LineMessage(
                    kind="text",
                    text=(
                        f"你送的餐券有朋友來店使用了!\n"
                        f"回饋 {payout} 點已入帳,目前 {sender.points_balance} 點。\n"
                        f"揪越多賺越多 🎁"
                    ),
                ),
            )
    return gift


__all__ = [
    "GIFT_REWARD_POINTS",
    "MONTHLY_REWARD_CAP_POINTS",
    "claim_gift",
    "create_gift",
    "redeem_gift",
]
