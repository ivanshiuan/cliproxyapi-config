"""Pydantic v2 schemas for the 成效報表 (``GET /membership/stats``).

Read-only aggregate snapshot across the four growth systems — membership/points,
stored value, referral flywheel, and UGC. All money/points are ``Decimal``;
percentages are 0-100 ``Decimal`` rounded to 2 dp.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from ..models import CustomerTier, UgcKind


class TierBucket(BaseModel):
    """How many live members sit in one loyalty tier."""

    model_config = ConfigDict(frozen=True)

    tier: CustomerTier
    count: int


class PointsStats(BaseModel):
    """Points programme health (outstanding liability + lifetime flow)."""

    model_config = ConfigDict(frozen=True)

    outstanding_balance: Decimal  # Σ cached points_balance (liability)
    lifetime_granted: Decimal  # Σ positive ledger deltas
    lifetime_redeemed: Decimal  # Σ |negative ledger deltas|


class StoredValueStats(BaseModel):
    """儲值 wallet health + penetration."""

    model_config = ConfigDict(frozen=True)

    outstanding_balance: Decimal  # Σ cached stored_value_balance (liability)
    members_with_balance: int
    penetration_pct: Decimal  # members_with_balance / total_members * 100
    lifetime_topup: Decimal  # Σ paid-in (reason="topup")
    lifetime_bonus: Decimal  # Σ promotional bonus (reason="topup.bonus")


class ReferralStats(BaseModel):
    """裂變 flywheel health."""

    model_config = ConfigDict(frozen=True)

    total: int
    pending: int
    qualified: int
    distinct_referrers: int
    conversion_pct: Decimal  # qualified / total * 100
    # Avg qualified new members generated per live member (viral proxy).
    k_factor: Decimal


class UgcKindBucket(BaseModel):
    """Submission count for one UGC content kind."""

    model_config = ConfigDict(frozen=True)

    kind: UgcKind
    count: int


class UgcStats(BaseModel):
    """UGC 換獎 queue health."""

    model_config = ConfigDict(frozen=True)

    pending: int
    approved: int
    rejected: int
    approval_pct: Decimal  # approved / (approved + rejected) * 100
    by_kind: list[UgcKindBucket]


class MembershipStatsResponse(BaseModel):
    """The full 成效報表 dashboard payload."""

    model_config = ConfigDict(frozen=True)

    total_members: int
    tiers: list[TierBucket]
    points: PointsStats
    stored_value: StoredValueStats
    referral: ReferralStats
    ugc: UgcStats


__all__ = [
    "MembershipStatsResponse",
    "PointsStats",
    "ReferralStats",
    "StoredValueStats",
    "TierBucket",
    "UgcKindBucket",
    "UgcStats",
]
