"""成效報表 — read-only analytics across the four growth systems.

One ``compute_stats`` call fans out a handful of tenant-scoped aggregate
queries (no new tables) and assembles the dashboard payload. Pure reads — no
``flush``/``commit``, no side effects.

Time window:
- ``since`` (derived from the ``?days=N`` query param) windows the **flow**
  metrics — points granted/redeemed, stored-value top-ups, referral edges, and
  UGC submissions are counted only from ``created_at >= since``.
- **Snapshot** metrics — outstanding balances, tier distribution, members with a
  wallet balance — are always point-in-time (a window can't change "what we owe
  right now"). ``window_days`` echoes the filter back to the caller.

Metric definitions worth pinning down:
- **Points liability** = Σ cached ``points_balance`` (what we'd owe if everyone
  redeemed today). Granted/redeemed come from the append-only ledger.
- **Stored-value penetration** = share of live members holding a positive wallet
  balance — the lock-in reach of the 儲值 mechanic.
- **Referral conversion** = qualified / total edges. **K-factor** = qualified
  referrals per live member (a pragmatic virality proxy, not invitesxCVR).
- **UGC approval rate** = approved / (approved + rejected); pending excluded so a
  growing backlog doesn't depress the rate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    Customer,
    CustomerPointsLedger,
    CustomerStoredValueLedger,
    Referral,
    ReferralStatus,
    UgcStatus,
    UgcSubmission,
)
from ..schemas.membership_stats import (
    MembershipStatsResponse,
    PointsStats,
    ReferralStats,
    StoredValueStats,
    TierBucket,
    UgcKindBucket,
    UgcStats,
)

_ZERO = Decimal("0")


def _pct(numerator: Decimal | int, denominator: Decimal | int) -> Decimal:
    """numerator/denominator as a 0-100 percentage, 2 dp, divide-by-zero safe."""
    denom = Decimal(denominator)
    if denom == 0:
        return _ZERO
    return (Decimal(numerator) / denom * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _sum_when(condition: ColumnElement[bool], value: Any) -> ColumnElement[Any]:
    """``COALESCE(SUM(CASE WHEN condition THEN value ELSE 0 END), 0)``."""
    return func.coalesce(func.sum(case((condition, value), else_=_ZERO)), _ZERO)


def _count_when(condition: ColumnElement[bool]) -> ColumnElement[Any]:
    """``COALESCE(SUM(CASE WHEN condition THEN 1 ELSE 0 END), 0)``."""
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


async def compute_stats(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: datetime | None = None,
    window_days: int | None = None,
) -> MembershipStatsResponse:
    """Assemble the 成效報表 snapshot. ``since`` windows the flow metrics."""
    # ── Members + tier distribution (snapshot) ───────────────────────────────
    tier_rows = (
        await session.execute(
            select(Customer.tier, func.count())
            .where(Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None))
            .group_by(Customer.tier)
        )
    ).all()
    tiers = [TierBucket(tier=t, count=c) for t, c in tier_rows]
    total_members = sum(c for _, c in tier_rows)

    # ── Points: outstanding (snapshot) + granted/redeemed (flow) ─────────────
    points_outstanding = (
        await session.execute(
            select(func.coalesce(func.sum(Customer.points_balance), _ZERO)).where(
                Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    points_stmt = select(
        _sum_when(CustomerPointsLedger.delta > 0, CustomerPointsLedger.delta),
        _sum_when(CustomerPointsLedger.delta < 0, -CustomerPointsLedger.delta),
    ).where(CustomerPointsLedger.tenant_id == tenant_id)
    if since is not None:
        points_stmt = points_stmt.where(CustomerPointsLedger.created_at >= since)
    granted, redeemed = (await session.execute(points_stmt)).one()
    points = PointsStats(
        outstanding_balance=points_outstanding,
        lifetime_granted=granted,
        lifetime_redeemed=redeemed,
    )

    # ── Stored value: balance/penetration (snapshot) + in-flow (flow) ────────
    sv_outstanding, sv_members = (
        await session.execute(
            select(
                func.coalesce(func.sum(Customer.stored_value_balance), _ZERO),
                _count_when(Customer.stored_value_balance > 0),
            ).where(Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None))
        )
    ).one()
    sv_stmt = select(
        _sum_when(
            CustomerStoredValueLedger.reason == "topup",
            CustomerStoredValueLedger.delta,
        ),
        _sum_when(
            CustomerStoredValueLedger.reason == "topup.bonus",
            CustomerStoredValueLedger.delta,
        ),
    ).where(CustomerStoredValueLedger.tenant_id == tenant_id)
    if since is not None:
        sv_stmt = sv_stmt.where(CustomerStoredValueLedger.created_at >= since)
    sv_topup, sv_bonus = (await session.execute(sv_stmt)).one()
    stored_value = StoredValueStats(
        outstanding_balance=sv_outstanding,
        members_with_balance=sv_members,
        penetration_pct=_pct(sv_members, total_members),
        lifetime_topup=sv_topup,
        lifetime_bonus=sv_bonus,
    )

    # ── Referral flywheel (flow) ─────────────────────────────────────────────
    ref_stmt = select(
        func.count(),
        _count_when(Referral.status == ReferralStatus.PENDING),
        _count_when(Referral.status == ReferralStatus.QUALIFIED),
        func.count(func.distinct(Referral.referrer_id)),
    ).where(Referral.tenant_id == tenant_id)
    if since is not None:
        ref_stmt = ref_stmt.where(Referral.created_at >= since)
    ref_total, ref_pending, ref_qualified, ref_referrers = (
        await session.execute(ref_stmt)
    ).one()
    referral = ReferralStats(
        total=ref_total,
        pending=ref_pending,
        qualified=ref_qualified,
        distinct_referrers=ref_referrers,
        conversion_pct=_pct(ref_qualified, ref_total),
        k_factor=(
            (Decimal(ref_qualified) / Decimal(total_members)).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
            if total_members
            else _ZERO
        ),
    )

    # ── UGC queue (flow) ─────────────────────────────────────────────────────
    ugc_stmt = select(
        _count_when(UgcSubmission.status == UgcStatus.PENDING),
        _count_when(UgcSubmission.status == UgcStatus.APPROVED),
        _count_when(UgcSubmission.status == UgcStatus.REJECTED),
    ).where(UgcSubmission.tenant_id == tenant_id)
    if since is not None:
        ugc_stmt = ugc_stmt.where(UgcSubmission.created_at >= since)
    ugc_pending, ugc_approved, ugc_rejected = (await session.execute(ugc_stmt)).one()

    kind_stmt = (
        select(UgcSubmission.kind, func.count())
        .where(UgcSubmission.tenant_id == tenant_id)
        .group_by(UgcSubmission.kind)
    )
    if since is not None:
        kind_stmt = kind_stmt.where(UgcSubmission.created_at >= since)
    ugc_kind_rows = (await session.execute(kind_stmt)).all()
    ugc = UgcStats(
        pending=ugc_pending,
        approved=ugc_approved,
        rejected=ugc_rejected,
        approval_pct=_pct(ugc_approved, ugc_approved + ugc_rejected),
        by_kind=[UgcKindBucket(kind=k, count=c) for k, c in ugc_kind_rows],
    )

    return MembershipStatsResponse(
        window_days=window_days,
        total_members=total_members,
        tiers=tiers,
        points=points,
        stored_value=stored_value,
        referral=referral,
        ugc=ugc,
    )


__all__ = ["compute_stats"]
