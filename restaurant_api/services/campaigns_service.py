"""Wheel-spin lottery engine — campaign config, the draw, and redemption.

Boundary rules (same as every other service):
- ``flush()`` only, never ``commit()`` — the request DI layer owns the txn.
- Business errors raise ``DomainError`` subclasses; the router doesn't translate.
- ``tenant_id`` is plumbed in from the request DI seam.

Concurrency
-----------
``spin`` locks the campaign row ``FOR UPDATE`` for its whole critical section,
which serialises spins within one campaign. That makes the daily-limit count
and the prize-quota decrement race-free without juggling multiple locks — the
right trade for an opening promo (one store, modest concurrency).

``redeem_voucher`` locks the voucher row and leans on the
``uq_vouchers_one_redeem_per_day`` partial unique index as the race-safe
backstop for "one redemption per member per visit/day".
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from random import Random
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..integrations.line import LineMessage, LineMessenger
from ..models import (
    CampaignPrize,
    CampaignSpin,
    CampaignStatus,
    CampaignVoucher,
    Customer,
    MarketingCampaign,
    VoucherStatus,
)
from ..schemas.campaigns import (
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    PrizeCreate,
    PrizeResponse,
    PrizeUpdate,
    PrizeWon,
    SpinRequest,
    SpinResponse,
    VoucherRedeemRequest,
    VoucherResponse,
    WheelResponse,
    WheelSegment,
)
from . import membership_service, referral_service
from .audit_service import audit

logger = logging.getLogger("restaurant_api.services.campaigns")

_TPE = ZoneInfo("Asia/Taipei")

# Process-wide RNG for the weighted draw. Tests inject a seeded ``Random`` via
# the ``rng`` parameter for deterministic outcomes.
_RNG = Random()

# Unambiguous voucher-code alphabet (no 0/O, 1/I) — easy to read off a screen
# and type at the counter.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _today_tpe() -> date:
    """Today's Asia/Taipei calendar date — the daily-limit / validity key."""
    return datetime.now(_TPE).date()


def _gen_code(n: int = 8) -> str:
    """Random voucher code from the unambiguous alphabet (no 0/O, 1/I)."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


# ──────────────────────────────────────────────────────────────────────────
# Campaign config
# ──────────────────────────────────────────────────────────────────────────


async def create_campaign(
    session: AsyncSession,
    payload: CampaignCreate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> CampaignResponse:
    """Create a campaign after enforcing per-tenant slug uniqueness; audited."""
    await _reject_duplicate_slug(session, tenant_id=tenant_id, slug=payload.slug)
    row = MarketingCampaign(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        slug=payload.slug,
        status=payload.status,
        starts_at=_as_utc(payload.starts_at),
        ends_at=_as_utc(payload.ends_at),
        daily_spin_limit=payload.daily_spin_limit,
        voucher_start_offset_days=payload.voucher_start_offset_days,
        voucher_validity_days=payload.voucher_validity_days,
        daily_message=payload.daily_message,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="campaign.created",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("marketing_campaigns", row.id),
        after={"name": payload.name, "slug": payload.slug, "status": payload.status.value},
    )
    return CampaignResponse.model_validate(row)


async def get_campaign(
    session: AsyncSession, campaign_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> CampaignResponse:
    """Load one campaign scoped to the tenant (404 if missing/soft-deleted)."""
    return CampaignResponse.model_validate(
        await _load_campaign(session, campaign_id, tenant_id)
    )


async def list_campaigns(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: CampaignStatus | None = None,
    include_deleted: bool = False,
    limit: int = 200,
) -> list[CampaignResponse]:
    """List tenant campaigns (newest first), optionally filtered by status."""
    stmt = select(MarketingCampaign).where(MarketingCampaign.tenant_id == tenant_id)
    if not include_deleted:
        stmt = stmt.where(MarketingCampaign.deleted_at.is_(None))
    if status is not None:
        stmt = stmt.where(MarketingCampaign.status == status)
    stmt = stmt.order_by(MarketingCampaign.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [CampaignResponse.model_validate(r) for r in rows]


async def patch_campaign(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> CampaignResponse:
    """Apply a partial campaign update, validate the window, and audit changes."""
    row = await _load_campaign(session, campaign_id, tenant_id)
    changed: dict[str, object] = {}
    for field in (
        "name",
        "status",
        "daily_spin_limit",
        "voucher_start_offset_days",
        "voucher_validity_days",
        "daily_message",
    ):
        new = getattr(payload, field)
        if new is not None and new != getattr(row, field):
            setattr(row, field, new)
            changed[field] = new.value if isinstance(new, CampaignStatus) else new
    for field in ("starts_at", "ends_at"):
        new_dt = getattr(payload, field)
        if new_dt is not None:
            setattr(row, field, _as_utc(new_dt))
            changed[field] = new_dt.isoformat()
    if row.starts_at and row.ends_at and row.ends_at <= row.starts_at:
        raise ValidationError(message="ends_at must be after starts_at")
    if not changed:
        return CampaignResponse.model_validate(row)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="campaign.updated",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("marketing_campaigns", row.id),
        after=changed,
    )
    return CampaignResponse.model_validate(row)


# ──────────────────────────────────────────────────────────────────────────
# Prizes
# ──────────────────────────────────────────────────────────────────────────


async def add_prize(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    payload: PrizeCreate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> PrizeResponse:
    """Add a prize / wheel segment to a campaign; audited."""
    await _load_campaign(session, campaign_id, tenant_id)
    row = CampaignPrize(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        name=payload.name,
        sort_order=payload.sort_order,
        weight=payload.weight,
        menu_item_id=payload.menu_item_id,
        value_estimate=payload.value_estimate,
        total_quota=payload.total_quota,
        daily_quota=payload.daily_quota,
        is_active=payload.is_active,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="campaign.prize_added",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("campaign_prizes", row.id),
        after={
            "campaign_id": str(campaign_id),
            "name": payload.name,
            "weight": payload.weight,
            "total_quota": payload.total_quota,
        },
    )
    return PrizeResponse.model_validate(row)


async def list_prizes(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    include_inactive: bool = True,
) -> list[PrizeResponse]:
    """List a campaign's prizes in wheel order (active-only when asked)."""
    await _load_campaign(session, campaign_id, tenant_id)
    stmt = (
        select(CampaignPrize)
        .where(
            CampaignPrize.campaign_id == campaign_id,
            CampaignPrize.tenant_id == tenant_id,
            CampaignPrize.deleted_at.is_(None),
        )
        .order_by(CampaignPrize.sort_order, CampaignPrize.created_at)
    )
    if not include_inactive:
        stmt = stmt.where(CampaignPrize.is_active.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return [PrizeResponse.model_validate(r) for r in rows]


async def patch_prize(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    prize_id: uuid.UUID,
    payload: PrizeUpdate,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> PrizeResponse:
    """Apply a partial prize update, recording changed fields in the audit log."""
    row = await _load_prize(session, campaign_id, prize_id, tenant_id)
    changed: dict[str, object] = {}
    for field in (
        "name",
        "sort_order",
        "weight",
        "menu_item_id",
        "value_estimate",
        "total_quota",
        "daily_quota",
        "is_active",
    ):
        new = getattr(payload, field)
        if new is not None and new != getattr(row, field):
            setattr(row, field, new)
            changed[field] = str(new) if isinstance(new, Decimal | uuid.UUID) else new
    if not changed:
        return PrizeResponse.model_validate(row)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="campaign.prize_updated",
        tenant_id=tenant_id,
        actor_id=actor_id,
        target=("campaign_prizes", row.id),
        after=changed,
    )
    return PrizeResponse.model_validate(row)


# ──────────────────────────────────────────────────────────────────────────
# Public wheel layout
# ──────────────────────────────────────────────────────────────────────────


async def get_wheel(
    session: AsyncSession, campaign_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> WheelResponse:
    """Build the public wheel layout (active prizes as labelled segments)."""
    campaign = await _load_campaign(session, campaign_id, tenant_id)
    prizes = await list_prizes(
        session, campaign_id, tenant_id=tenant_id, include_inactive=False
    )
    return WheelResponse(
        campaign_id=campaign.id,
        name=campaign.name,
        slug=campaign.slug,
        status=campaign.status,
        daily_spin_limit=campaign.daily_spin_limit,
        segments=[
            WheelSegment(id=p.id, name=p.name, sort_order=p.sort_order) for p in prizes
        ],
    )


# ──────────────────────────────────────────────────────────────────────────
# Spin
# ──────────────────────────────────────────────────────────────────────────


async def spin(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    payload: SpinRequest,
    *,
    tenant_id: uuid.UUID,
    messenger: LineMessenger | None = None,
    rng: Random | None = None,
) -> SpinResponse:
    """Spin the wheel once for a member.

    Serialised per-campaign via a ``FOR UPDATE`` lock so the daily-limit count
    and per-prize quota decrement are race-free.
    """
    draw_rng = rng or _RNG

    # Lock the campaign for the critical section.
    campaign = (
        await session.execute(
            select(MarketingCampaign)
            .where(
                MarketingCampaign.id == campaign_id,
                MarketingCampaign.tenant_id == tenant_id,
                MarketingCampaign.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise NotFoundError(
            message=f"campaign {campaign_id} not found",
            details={"campaign_id": str(campaign_id)},
        )

    _assert_spinnable(campaign)

    customer, is_new = await _resolve_or_create_customer(
        session, payload, tenant_id=tenant_id
    )
    # Activate loop: first-touch members get joining points immediately, so the
    # prospect leaves the wheel already holding wallet value (idempotent).
    if is_new:
        await membership_service.grant_welcome_bonus(
            session, customer, actor_id=payload.actor_id
        )
        # 裂變: if the friend arrived via a referral code, record the edge and
        # gift the new member. Qualification (referrer reward) fires later on
        # this member's first order close.
        if payload.ref:
            await referral_service.attribute_referral(
                session,
                referred=customer,
                code=payload.ref,
                tenant_id=tenant_id,
                actor_id=payload.actor_id,
            )

    today = _today_tpe()
    spins_today = await _count_spins_today(session, campaign_id, customer.id, today)
    if spins_today >= campaign.daily_spin_limit:
        raise ConflictError(
            message="daily spin limit reached for this member",
            details={
                "daily_spin_limit": campaign.daily_spin_limit,
                "spins_today": spins_today,
            },
        )

    chosen = await _draw_prize(session, campaign_id, tenant_id, today, draw_rng)
    is_win = chosen is not None and _is_win(chosen)

    spin_row = CampaignSpin(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        customer_id=customer.id,
        prize_id=chosen.id if chosen is not None else None,
        spin_date=today,
        won=is_win,
    )
    session.add(spin_row)
    await session.flush()

    voucher_resp: VoucherResponse | None = None
    prize_won: PrizeWon | None = None
    if is_win and chosen is not None:
        chosen.awarded_count += 1
        voucher = await _mint_voucher(
            session, campaign, customer, chosen, spin_row, today
        )
        voucher_resp = VoucherResponse.model_validate(voucher)
        prize_won = PrizeWon(
            prize_id=chosen.id,
            name=chosen.name,
            value_estimate=chosen.value_estimate,
            menu_item_id=chosen.menu_item_id,
        )
        await audit(
            session,
            action="campaign.voucher_issued",
            tenant_id=tenant_id,
            actor_id=payload.actor_id,
            target=("campaign_vouchers", voucher.id),
            after={
                "campaign_id": str(campaign_id),
                "customer_id": str(customer.id),
                "prize": chosen.name,
                "code": voucher.code,
                "valid_from": voucher.valid_from.isoformat(),
                "valid_until": voucher.valid_until.isoformat(),
            },
        )

    # Daily pop-up message — best-effort push to the member's LINE. A push
    # failure (LINE outage, HTTP messenger not yet wired) must NOT roll back the
    # spin and rob the member of their prize, so swallow + log it.
    daily_message = campaign.daily_message
    if messenger is not None and daily_message and customer.line_user_id:
        try:
            await messenger.push(
                customer.line_user_id,
                LineMessage(kind="text", text=daily_message),
            )
        except Exception:  # best-effort side channel; never fail the spin
            logger.warning(
                "campaign.daily_message_push_failed",
                extra={"campaign_id": str(campaign_id), "customer_id": str(customer.id)},
            )

    return SpinResponse(
        spin_id=spin_row.id,
        campaign_id=campaign_id,
        customer_id=customer.id,
        is_new_member=is_new,
        won=is_win,
        prize=prize_won,
        voucher=voucher_resp,
        daily_message=daily_message,
        spins_remaining_today=max(0, campaign.daily_spin_limit - (spins_today + 1)),
    )


# ──────────────────────────────────────────────────────────────────────────
# Vouchers / redemption
# ──────────────────────────────────────────────────────────────────────────


async def list_vouchers(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    status: VoucherStatus | None = None,
    limit: int = 200,
) -> list[VoucherResponse]:
    """The member's wallet — best (highest value) first so the counter sees the
    grand prize at the top."""
    stmt = (
        select(CampaignVoucher)
        .where(
            CampaignVoucher.campaign_id == campaign_id,
            CampaignVoucher.tenant_id == tenant_id,
            CampaignVoucher.customer_id == customer_id,
        )
        .order_by(
            CampaignVoucher.value_estimate.desc(),
            CampaignVoucher.created_at.desc(),
        )
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(CampaignVoucher.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [VoucherResponse.model_validate(r) for r in rows]


async def get_voucher_by_code(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    code: str,
    *,
    tenant_id: uuid.UUID,
) -> VoucherResponse:
    """Look up a voucher by its (case-insensitive) redemption code; 404 if none."""
    row = (
        await session.execute(
            select(CampaignVoucher).where(
                CampaignVoucher.campaign_id == campaign_id,
                CampaignVoucher.tenant_id == tenant_id,
                CampaignVoucher.code == code.upper(),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"voucher code {code!r} not found",
            details={"code": code},
        )
    return VoucherResponse.model_validate(row)


async def redeem_voucher(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    voucher_id: uuid.UUID,
    payload: VoucherRedeemRequest,
    *,
    tenant_id: uuid.UUID,
) -> VoucherResponse:
    """Redeem one voucher at the counter.

    Enforces "one redemption per member per visit/day": a second redemption on
    the same Asia/Taipei day for the same campaign is rejected (clean envelope
    here, with the partial unique index as the race-safe backstop).
    """
    voucher = (
        await session.execute(
            select(CampaignVoucher)
            .where(
                CampaignVoucher.id == voucher_id,
                CampaignVoucher.campaign_id == campaign_id,
                CampaignVoucher.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if voucher is None:
        raise NotFoundError(
            message=f"voucher {voucher_id} not found",
            details={"voucher_id": str(voucher_id)},
        )

    today = _today_tpe()
    if voucher.status == VoucherStatus.REDEEMED:
        raise ConflictError(
            message="voucher already redeemed",
            details={"redeemed_on": voucher.redeemed_on.isoformat() if voucher.redeemed_on else None},
        )
    if voucher.status == VoucherStatus.VOID:
        raise ConflictError(message="voucher has been voided")
    if today < voucher.valid_from:
        raise ConflictError(
            message="voucher not yet valid",
            details={"valid_from": voucher.valid_from.isoformat()},
        )
    if today > voucher.valid_until or voucher.status == VoucherStatus.EXPIRED:
        # Lazily mark it expired so the wallet reflects reality.
        voucher.status = VoucherStatus.EXPIRED
        await session.flush()
        raise ConflictError(
            message="voucher expired",
            details={"valid_until": voucher.valid_until.isoformat()},
        )

    # In-service guard for a clean message (the unique index is the backstop).
    already = (
        await session.execute(
            select(CampaignVoucher.id).where(
                CampaignVoucher.campaign_id == campaign_id,
                CampaignVoucher.customer_id == voucher.customer_id,
                CampaignVoucher.redeemed_on == today,
                CampaignVoucher.status == VoucherStatus.REDEEMED,
            )
        )
    ).first()
    if already is not None:
        raise ConflictError(
            message="member already redeemed a voucher today (one per visit)",
            details={"redeemed_on": today.isoformat()},
        )

    voucher.status = VoucherStatus.REDEEMED
    voucher.redeemed_at = datetime.now(UTC)
    voucher.redeemed_on = today
    voucher.redeemed_order_id = payload.order_id
    voucher.redeemed_by = payload.actor_id
    try:
        await session.flush()
    except IntegrityError as exc:
        # Lost the race on uq_vouchers_one_redeem_per_day.
        raise ConflictError(
            message="member already redeemed a voucher today (one per visit)",
            details={"redeemed_on": today.isoformat()},
        ) from exc
    await session.refresh(voucher)

    await audit(
        session,
        action="campaign.voucher_redeemed",
        tenant_id=tenant_id,
        actor_id=payload.actor_id,
        target=("campaign_vouchers", voucher.id),
        after={
            "code": voucher.code,
            "prize": voucher.prize_name,
            "order_id": str(payload.order_id) if payload.order_id else None,
            "redeemed_on": today.isoformat(),
        },
        reason=payload.note,
    )
    return VoucherResponse.model_validate(voucher)


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a datetime to tz-aware UTC for DB storage (naive → assumed UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _is_win(prize: CampaignPrize) -> bool:
    """A prize mints a voucher only if it's a real reward — a "no-win" wheel
    segment carries no menu item and zero value."""
    return prize.menu_item_id is not None or prize.value_estimate > 0


def _assert_spinnable(campaign: MarketingCampaign) -> None:
    """Raise ``ConflictError`` unless the campaign is active and within its window."""
    if campaign.status != CampaignStatus.ACTIVE:
        raise ConflictError(
            message="campaign is not active",
            details={"status": campaign.status.value},
        )
    now = datetime.now(UTC)
    if campaign.starts_at and now < campaign.starts_at:
        raise ConflictError(
            message="campaign has not started yet",
            details={"starts_at": campaign.starts_at.isoformat()},
        )
    if campaign.ends_at and now > campaign.ends_at:
        raise ConflictError(
            message="campaign has ended",
            details={"ends_at": campaign.ends_at.isoformat()},
        )


async def _draw_prize(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    tenant_id: uuid.UUID,
    today: date,
    rng: Random,
) -> CampaignPrize | None:
    """Weighted random draw over eligible prizes (locked ``FOR UPDATE``).

    A prize is eligible when it's active, has positive weight, and hasn't hit
    its total/daily quota. Returns ``None`` when nothing is eligible (treated
    as a no-win spin).
    """
    prizes = (
        await session.execute(
            select(CampaignPrize)
            .where(
                CampaignPrize.campaign_id == campaign_id,
                CampaignPrize.tenant_id == tenant_id,
                CampaignPrize.deleted_at.is_(None),
                CampaignPrize.is_active.is_(True),
                CampaignPrize.weight > 0,
            )
            .order_by(CampaignPrize.sort_order, CampaignPrize.id)
            .with_for_update()
        )
    ).scalars().all()

    # One grouped query for all daily-capped prizes (vs one per prize) keeps the
    # locked section short.
    quota_prize_ids = [p.id for p in prizes if p.daily_quota is not None]
    daily_counts: dict[uuid.UUID, int] = {}
    if quota_prize_ids:
        result = await session.execute(
            select(CampaignSpin.prize_id, func.count())
            .where(
                CampaignSpin.spin_date == today,
                CampaignSpin.won.is_(True),
                CampaignSpin.prize_id.in_(quota_prize_ids),
            )
            .group_by(CampaignSpin.prize_id)
        )
        daily_counts = {pid: cnt for pid, cnt in result.tuples() if pid is not None}

    eligible: list[CampaignPrize] = []
    for p in prizes:
        if p.total_quota is not None and p.awarded_count >= p.total_quota:
            continue
        if p.daily_quota is not None and daily_counts.get(p.id, 0) >= p.daily_quota:
            continue
        eligible.append(p)

    if not eligible:
        return None
    total = sum(p.weight for p in eligible)
    if total <= 0:
        return None
    threshold = rng.random() * total
    cumulative = 0
    for p in eligible:
        cumulative += p.weight
        if threshold <= cumulative:
            return p
    return eligible[-1]


async def _mint_voucher(
    session: AsyncSession,
    campaign: MarketingCampaign,
    customer: Customer,
    prize: CampaignPrize,
    spin_row: CampaignSpin,
    today: date,
) -> CampaignVoucher:
    """Mint an active voucher for a won prize, computing its validity window."""
    valid_from = today + timedelta(days=campaign.voucher_start_offset_days)
    valid_until = valid_from + timedelta(days=campaign.voucher_validity_days)
    voucher = CampaignVoucher(
        tenant_id=campaign.tenant_id,
        campaign_id=campaign.id,
        customer_id=customer.id,
        prize_id=prize.id,
        spin_id=spin_row.id,
        code=await _unique_code(session, campaign.tenant_id),
        status=VoucherStatus.ACTIVE,
        prize_name=prize.name,
        value_estimate=prize.value_estimate,
        menu_item_id=prize.menu_item_id,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    session.add(voucher)
    await session.flush()
    return voucher


async def _unique_code(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    """Generate a code not already used by this tenant. 32^8 keyspace makes a
    collision astronomically unlikely; we still check a few times to be safe."""
    for _ in range(5):
        code = _gen_code()
        hit = (
            await session.execute(
                select(CampaignVoucher.id).where(
                    CampaignVoucher.tenant_id == tenant_id,
                    CampaignVoucher.code == code,
                )
            )
        ).first()
        if hit is None:
            return code
    # Extremely unlikely — widen the code rather than fail the spin.
    return _gen_code(12)


async def _count_spins_today(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    customer_id: uuid.UUID,
    today: date,
) -> int:
    """Count a member's spins in this campaign for the given day (limit check)."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(CampaignSpin)
                .where(
                    CampaignSpin.campaign_id == campaign_id,
                    CampaignSpin.customer_id == customer_id,
                    CampaignSpin.spin_date == today,
                )
            )
        ).scalar_one()
    )


async def _resolve_or_create_customer(
    session: AsyncSession,
    payload: SpinRequest,
    *,
    tenant_id: uuid.UUID,
) -> tuple[Customer, bool]:
    """Find the member by id or handle; create one on first spin (加入會員)."""
    if payload.customer_id is not None:
        row = (
            await session.execute(
                select(Customer).where(
                    Customer.id == payload.customer_id,
                    Customer.tenant_id == tenant_id,
                    Customer.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                message=f"customer {payload.customer_id} not found",
                details={"customer_id": str(payload.customer_id)},
            )
        return row, False

    conditions = []
    if payload.line_user_id:
        conditions.append(Customer.line_user_id == payload.line_user_id)
    if payload.phone:
        conditions.append(Customer.phone == payload.phone)
    existing = (
        await session.execute(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
                or_(*conditions),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    row = Customer(
        tenant_id=tenant_id,
        display_name=payload.display_name or "輪盤會員",
        line_user_id=payload.line_user_id,
        phone=payload.phone,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="customer.created",
        tenant_id=tenant_id,
        target=("customers", row.id),
        after={"source": "campaign_spin", "has_line": payload.line_user_id is not None},
    )
    return row, True


async def _load_campaign(
    session: AsyncSession, campaign_id: uuid.UUID, tenant_id: uuid.UUID
) -> MarketingCampaign:
    """Load a non-deleted campaign scoped to the tenant, or raise ``NotFoundError``."""
    row = (
        await session.execute(
            select(MarketingCampaign).where(
                MarketingCampaign.id == campaign_id,
                MarketingCampaign.tenant_id == tenant_id,
                MarketingCampaign.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"campaign {campaign_id} not found",
            details={"campaign_id": str(campaign_id)},
        )
    return row


async def _load_prize(
    session: AsyncSession,
    campaign_id: uuid.UUID,
    prize_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> CampaignPrize:
    """Load a non-deleted prize scoped to its campaign + tenant, or 404."""
    row = (
        await session.execute(
            select(CampaignPrize).where(
                CampaignPrize.id == prize_id,
                CampaignPrize.campaign_id == campaign_id,
                CampaignPrize.tenant_id == tenant_id,
                CampaignPrize.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"prize {prize_id} not found",
            details={"prize_id": str(prize_id)},
        )
    return row


async def _reject_duplicate_slug(
    session: AsyncSession, *, tenant_id: uuid.UUID, slug: str
) -> None:
    """Raise ``ConflictError`` if the slug is already used by a live campaign."""
    hit = (
        await session.execute(
            select(MarketingCampaign.id).where(
                MarketingCampaign.tenant_id == tenant_id,
                MarketingCampaign.slug == slug,
                MarketingCampaign.deleted_at.is_(None),
            )
        )
    ).first()
    if hit is not None:
        raise ConflictError(
            message=f"slug {slug!r} already in use by another campaign",
            details={"slug": slug},
        )


__all__ = [
    "add_prize",
    "create_campaign",
    "get_campaign",
    "get_voucher_by_code",
    "get_wheel",
    "list_campaigns",
    "list_prizes",
    "list_vouchers",
    "patch_campaign",
    "patch_prize",
    "redeem_voucher",
    "spin",
]
