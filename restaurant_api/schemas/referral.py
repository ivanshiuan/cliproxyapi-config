"""Pydantic v2 schemas for the 裂變 (referral) endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models import ReferralStatus


class ReferralCodeResponse(BaseModel):
    """A member's shareable referral code (minted on first request)."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: UUID
    referral_code: str


class ReferralResponse(BaseModel):
    """One referral edge — read-only projection for the referrer's dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    referrer_id: UUID
    referred_id: UUID
    status: ReferralStatus
    qualifying_order_id: UUID | None
    referrer_reward_points: Decimal
    qualified_at: datetime | None
    created_at: datetime


__all__ = ["ReferralCodeResponse", "ReferralResponse"]
