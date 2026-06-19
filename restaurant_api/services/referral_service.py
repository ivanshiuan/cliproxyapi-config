"""裂變飛輪 — referral (member-get-member) service.

The circle the operator asked for: a member shares their code → a friend signs up
with it (friend gets a welcome gift) → the friend makes their first spend → the
referrer earns override points → which they spend in store → which makes them
share again. Two-sided rewards, fully automated:

  1. ``ensure_referral_code`` — lazily mint a member's unique shareable code.
  2. ``attribute_referral`` — on signup-with-code, record the edge (``pending``)
     and drop a welcome gift into the *referred* member's wallet.
  3. ``qualify_referral`` — on the referred member's first qualifying order, flip
     ``pending → qualified`` and grant the *referrer* override points + LINE.

Correctness:
- A member can be referred **once** (unique index on ``referred_id``); a second
  attribution attempt is a silent no-op.
- Self-referral and unknown codes are ignored (no edge created).
- Qualification is one-shot — guarded by the ``pending`` status, so the referrer
  bonus can never be double-granted.
- ``flush()`` only; the caller owns the transaction.
"""

from __future__ import annotations

import contextlib
import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import NotFoundError
from ..integrations.line import LineMessage, LineMessenger, get_messenger
from ..models import Customer, CustomerPointsLedger, Referral, ReferralStatus
from .audit_service import audit

# Tunable rewards (move to tenant_settings per-brand later).
REFERRED_SIGNUP_POINTS = Decimal("100")  # 被推薦人註冊禮
REFERRER_REWARD_POINTS = Decimal("200")  # 推薦人在朋友首次消費後的回饋

_CODE_LEN = 8
# Unambiguous alphabet — no 0/O/1/I/L to avoid mis-keying a shared code.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_MAX_TRIES = 6


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


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


async def ensure_referral_code(
    session: AsyncSession, customer: Customer, *, tenant_id: uuid.UUID
) -> str:
    """Return the member's referral code, minting a unique one on first use."""
    if customer.referral_code:
        return customer.referral_code

    for _ in range(_CODE_MAX_TRIES):
        candidate = _new_code()
        try:
            async with session.begin_nested():
                customer.referral_code = candidate
                await session.flush()
            return candidate
        except IntegrityError:
            customer.referral_code = None  # collided with an existing code; retry
    raise RuntimeError("could not allocate a unique referral code")


async def get_or_create_code(
    session: AsyncSession, customer_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """Load a live member and return (id, referral_code), minting on first use."""
    customer = (
        await session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError(
            message=f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    code = await ensure_referral_code(session, customer, tenant_id=tenant_id)
    return customer.id, code


async def attribute_referral(
    session: AsyncSession,
    *,
    referred: Customer,
    code: str,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> Referral | None:
    """Record a referral edge on signup-with-code; gift the referred member.

    No-op (returns ``None``) for an unknown code, a self-referral, or a member
    who was already referred.
    """
    normalized = code.strip().upper()
    referrer = (
        await session.execute(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.referral_code == normalized,
                Customer.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if referrer is None or referrer.id == referred.id:
        return None

    referral = Referral(
        tenant_id=tenant_id,
        referrer_id=referrer.id,
        referred_id=referred.id,
        status=ReferralStatus.PENDING,
    )
    try:
        async with session.begin_nested():
            session.add(referral)
            await session.flush()
    except IntegrityError:
        return None  # referred member already has a referral edge

    await _grant_points(
        session,
        referred,
        delta=REFERRED_SIGNUP_POINTS,
        reason="referral.signup",
        note=f"referred by {referrer.id}",
    )
    await session.flush()
    await audit(
        session,
        action="referral.attributed",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("referrals", referral.id),
        after={
            "referrer_id": str(referrer.id),
            "referred_id": str(referred.id),
            "signup_points": str(REFERRED_SIGNUP_POINTS),
        },
    )
    return referral


async def qualify_referral(
    session: AsyncSession,
    *,
    referred_id: uuid.UUID,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
    messenger: LineMessenger | None = None,
) -> Referral | None:
    """Qualify a pending referral on the referred member's first spend.

    Flips ``pending → qualified``, grants the referrer override points, and
    pushes them a LINE nudge. No-op if there's no pending referral.
    """
    referral = (
        await session.execute(
            select(Referral)
            .where(
                Referral.referred_id == referred_id,
                Referral.tenant_id == tenant_id,
                Referral.status == ReferralStatus.PENDING,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if referral is None:
        return None

    referrer = (
        await session.execute(
            select(Customer).where(
                Customer.id == referral.referrer_id,
                Customer.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    referral.status = ReferralStatus.QUALIFIED
    referral.qualifying_order_id = order_id
    referral.qualified_at = datetime.now(UTC)
    referral.referrer_reward_points = REFERRER_REWARD_POINTS

    if referrer is not None:
        await _grant_points(
            session,
            referrer,
            delta=REFERRER_REWARD_POINTS,
            reason="referral.bonus",
            note=f"qualified by {referred_id}",
        )
    await session.flush()
    await audit(
        session,
        action="referral.qualified",
        tenant_id=tenant_id,
        target=("referrals", referral.id),
        after={
            "referrer_id": str(referral.referrer_id),
            "referred_id": str(referred_id),
            "reward_points": str(REFERRER_REWARD_POINTS),
            "order_id": str(order_id),
        },
    )

    out = messenger or get_messenger()
    if referrer is not None and referrer.line_user_id:
        with contextlib.suppress(Exception):  # best-effort
            await out.push(
                referrer.line_user_id,
                LineMessage(
                    kind="text",
                    text=(
                        f"你推薦的朋友來消費了!\n"
                        f"送你 {REFERRER_REWARD_POINTS} 點回饋 "
                        f"快揪更多朋友一起賺 🎁"
                    ),
                ),
            )
    return referral


async def list_referrals_for(
    session: AsyncSession,
    referrer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    limit: int = 200,
) -> list[Referral]:
    """The referrer's own referral edges, newest first (for their dashboard)."""
    stmt = (
        select(Referral)
        .where(
            Referral.referrer_id == referrer_id,
            Referral.tenant_id == tenant_id,
        )
        .order_by(Referral.created_at.desc(), Referral.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "REFERRED_SIGNUP_POINTS",
    "REFERRER_REWARD_POINTS",
    "attribute_referral",
    "ensure_referral_code",
    "get_or_create_code",
    "list_referrals_for",
    "qualify_referral",
]
