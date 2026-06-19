"""成效報表 — read-only analytics across the four growth systems.

One ``compute_stats`` call fans out a handful of tenant-scoped aggregate
queries (no new tables) and assembles the dashboard payload. Pure reads — no
``flush``/``commit``, no side effects.

Metric definitions worth pinning down:
- **Points liability** = Σ cached ``points_balance`` (what we'd owe if everyone
  redeemed today). Lifetime granted/redeemed come from the append-only ledger.
- **Stored-value penetration** = share of live members holding a positive wallet
  balance — the lock-in reach of the 儲值 mechanic.
- **Referral conversion** = qualified / total edges. **K-factor** = qualified
  referrals per live member (a pragmatic virality proxy, not invitesxCVR).
- **UGC approval rate** = approved / (approved + rejected); pending excluded so a
  growing backlog doesn't depress the rate.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def compute_stats(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> MembershipStatsResponse:
    """Assemble the full 成效報表 snapshot for one tenant."""
    # ── Members + tier distribution ──────────────────────────────────────────
    tier_rows = (
        await session.execute(
            select(Customer.tier, func.count())
            .where(Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None))
            .group_by(Customer.tier)
        )
    ).all()
    tiers = [TierBucket(tier=t, count=c) for t, c in tier_rows]
    total_members = sum(c for _, c in tier_rows)

    # ── Points: outstanding liability + lifetime flow ────────────────────────
    points_outstanding = (
        await session.execute(
            select(func.coalesce(func.sum(Customer.points_balance), _ZERO)).where(
                Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    granted, redeemed = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (CustomerPointsLedger.delta > 0, CustomerPointsLedger.delta),
                            else_=_ZERO,
                        )
                    ),
                    _ZERO,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (CustomerPointsLedger.delta < 0, -CustomerPointsLedger.delta),
                            else_=_ZERO,
                        )
                    ),
                    _ZERO,
                ),
            ).where(CustomerPointsLedger.tenant_id == tenant_id)
        )
    ).one()
    points = PointsStats(
        outstanding_balance=points_outstanding,
        lifetime_granted=granted,
        lifetime_redeemed=redeemed,
    )

    # ── Stored value: liability + penetration + lifetime in-flow ─────────────
    sv_outstanding, sv_members = (
        await session.execute(
            select(
                func.coalesce(func.sum(Customer.stored_value_balance), _ZERO),
                func.coalesce(
                    func.sum(
                        case((Customer.stored_value_balance > 0, 1), else_=0)
                    ),
                    0,
                ),
            ).where(Customer.tenant_id == tenant_id, Customer.deleted_at.is_(None))
        )
    ).one()
    sv_topup, sv_bonus = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CustomerStoredValueLedger.reason == "topup",
                                CustomerStoredValueLedger.delta,
                            ),
                            else_=_ZERO,
                        )
                    ),
                    _ZERO,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CustomerStoredValueLedger.reason == "topup.bonus",
                                CustomerStoredValueLedger.delta,
                            ),
                            else_=_ZERO,
                        )
                    ),
                    _ZERO,
                ),
            ).where(CustomerStoredValueLedger.tenant_id == tenant_id)
        )
    ).one()
    stored_value = StoredValueStats(
        outstanding_balance=sv_outstanding,
        members_with_balance=sv_members,
        penetration_pct=_pct(sv_members, total_members),
        lifetime_topup=sv_topup,
        lifetime_bonus=sv_bonus,
    )

    # ── Referral flywheel ────────────────────────────────────────────────────
    ref_total, ref_pending, ref_qualified, ref_referrers = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(
                        case((Referral.status == ReferralStatus.PENDING, 1), else_=0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((Referral.status == ReferralStatus.QUALIFIED, 1), else_=0)
                    ),
                    0,
                ),
                func.count(func.distinct(Referral.referrer_id)),
            ).where(Referral.tenant_id == tenant_id)
        )
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

    # ── UGC queue ────────────────────────────────────────────────────────────
    ugc_pending, ugc_approved, ugc_rejected = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case((UgcSubmission.status == UgcStatus.PENDING, 1), else_=0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((UgcSubmission.status == UgcStatus.APPROVED, 1), else_=0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((UgcSubmission.status == UgcStatus.REJECTED, 1), else_=0)
                    ),
                    0,
                ),
            ).where(UgcSubmission.tenant_id == tenant_id)
        )
    ).one()
    ugc_kind_rows = (
        await session.execute(
            select(UgcSubmission.kind, func.count())
            .where(UgcSubmission.tenant_id == tenant_id)
            .group_by(UgcSubmission.kind)
        )
    ).all()
    ugc = UgcStats(
        pending=ugc_pending,
        approved=ugc_approved,
        rejected=ugc_rejected,
        approval_pct=_pct(ugc_approved, ugc_approved + ugc_rejected),
        by_kind=[UgcKindBucket(kind=k, count=c) for k, c in ugc_kind_rows],
    )

    return MembershipStatsResponse(
        total_members=total_members,
        tiers=tiers,
        points=points,
        stored_value=stored_value,
        referral=referral,
        ugc=ugc,
    )


__all__ = ["compute_stats"]
