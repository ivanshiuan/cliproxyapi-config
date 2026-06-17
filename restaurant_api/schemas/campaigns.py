"""Pydantic v2 schemas for the ``/campaigns`` router (wheel-spin lottery).

Surface split:
- Config side (operator): create/update a campaign + its prizes.
- Player side: spin the wheel, read the public wheel layout.
- Wallet side: list a member's vouchers, redeem one at the counter.

Money fields use ``StrictDecimal`` (rejects ``float`` at the boundary) per the
project money rule.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ..models import CampaignStatus, VoucherStatus


def _reject_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("float not allowed; use Decimal or string")
    return v


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


# ──────────────────────────────────────────────────────────────────────────
# Campaign config
# ──────────────────────────────────────────────────────────────────────────


class CampaignCreate(BaseModel):
    """Create a campaign (starts in ``draft`` unless a status is supplied)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    store_id: UUID | None = None
    status: CampaignStatus = CampaignStatus.DRAFT
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    daily_spin_limit: int = Field(default=1, ge=1, le=100)
    voucher_start_offset_days: int = Field(default=0, ge=0, le=365)
    voucher_validity_days: int = Field(default=14, ge=1, le=365)
    daily_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_window(self) -> CampaignCreate:
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class CampaignUpdate(BaseModel):
    """Partial update. Only supplied fields are applied."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: CampaignStatus | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    daily_spin_limit: int | None = Field(default=None, ge=1, le=100)
    voucher_start_offset_days: int | None = Field(default=None, ge=0, le=365)
    voucher_validity_days: int | None = Field(default=None, ge=1, le=365)
    daily_message: str | None = Field(default=None, max_length=2000)


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    name: str
    slug: str
    status: CampaignStatus
    starts_at: datetime | None
    ends_at: datetime | None
    daily_spin_limit: int
    voucher_start_offset_days: int
    voucher_validity_days: int
    daily_message: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Prizes
# ──────────────────────────────────────────────────────────────────────────


class PrizeCreate(BaseModel):
    """Add a wheel segment.

    A "no-win" segment is just ``menu_item_id = None`` + ``value_estimate = 0``.
    Make the grand prize scarce with a small ``weight`` AND a small
    ``total_quota``.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=120)
    sort_order: int = Field(default=0, ge=0, le=1000)
    weight: int = Field(default=1, ge=0, le=1_000_000)
    menu_item_id: UUID | None = None
    value_estimate: StrictDecimal = Field(default=Decimal("0"), ge=0)
    total_quota: int | None = Field(default=None, ge=0, le=10_000_000)
    daily_quota: int | None = Field(default=None, ge=0, le=10_000_000)
    is_active: bool = True


class PrizeUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=1000)
    weight: int | None = Field(default=None, ge=0, le=1_000_000)
    menu_item_id: UUID | None = None
    value_estimate: StrictDecimal | None = Field(default=None, ge=0)
    total_quota: int | None = Field(default=None, ge=0, le=10_000_000)
    daily_quota: int | None = Field(default=None, ge=0, le=10_000_000)
    is_active: bool | None = None


class PrizeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    name: str
    sort_order: int
    weight: int
    menu_item_id: UUID | None
    value_estimate: Decimal
    total_quota: int | None
    daily_quota: int | None
    awarded_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Public wheel layout (what the front-end renders)
# ──────────────────────────────────────────────────────────────────────────


class WheelSegment(BaseModel):
    """One slice on the rendered wheel. Odds intentionally omitted — clients
    only need labels; the server owns the draw."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sort_order: int


class WheelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    campaign_id: UUID
    name: str
    slug: str
    status: CampaignStatus
    daily_spin_limit: int
    segments: list[WheelSegment]


# ──────────────────────────────────────────────────────────────────────────
# Spin
# ──────────────────────────────────────────────────────────────────────────


class SpinRequest(BaseModel):
    """Spin the wheel for a member.

    Identify the member by ``customer_id`` (existing member) OR by a handle
    (``line_user_id`` / ``phone``) — if no member matches a handle, one is
    created on the spot (register-on-first-spin, the 加入會員 hook). When
    creating, ``display_name`` is used (falls back to a placeholder).
    """

    model_config = ConfigDict(frozen=True)

    customer_id: UUID | None = None
    line_user_id: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    actor_id: UUID | None = None

    @model_validator(mode="after")
    def _need_identity(self) -> SpinRequest:
        if not any((self.customer_id, self.line_user_id, self.phone)):
            raise ValueError(
                "provide customer_id, or a line_user_id / phone to identify "
                "(or auto-register) the member"
            )
        return self


class PrizeWon(BaseModel):
    """Compact description of what the wheel landed on."""

    model_config = ConfigDict(frozen=True)

    prize_id: UUID
    name: str
    value_estimate: Decimal
    menu_item_id: UUID | None


class SpinResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    spin_id: UUID
    campaign_id: UUID
    customer_id: UUID
    is_new_member: bool
    won: bool
    prize: PrizeWon | None
    voucher: VoucherResponse | None
    daily_message: str | None
    spins_remaining_today: int


# ──────────────────────────────────────────────────────────────────────────
# Vouchers / redemption
# ──────────────────────────────────────────────────────────────────────────


class VoucherResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    customer_id: UUID
    prize_id: UUID
    code: str
    status: VoucherStatus
    prize_name: str
    value_estimate: Decimal
    menu_item_id: UUID | None
    valid_from: date
    valid_until: date
    redeemed_at: datetime | None
    redeemed_on: date | None
    redeemed_order_id: UUID | None
    created_at: datetime


class VoucherRedeemRequest(BaseModel):
    """Redeem one voucher at the counter.

    The service enforces "one redemption per member per visit/day" — a second
    redemption on the same Asia/Taipei day for the same campaign is rejected.
    """

    model_config = ConfigDict(frozen=True)

    actor_id: UUID | None = None
    order_id: UUID | None = None
    note: str | None = Field(default=None, max_length=255)


# SpinResponse references VoucherResponse defined below it.
SpinResponse.model_rebuild()


__all__ = [
    "CampaignCreate",
    "CampaignResponse",
    "CampaignUpdate",
    "PrizeCreate",
    "PrizeResponse",
    "PrizeUpdate",
    "PrizeWon",
    "SpinRequest",
    "SpinResponse",
    "VoucherRedeemRequest",
    "VoucherResponse",
    "WheelResponse",
    "WheelSegment",
]
