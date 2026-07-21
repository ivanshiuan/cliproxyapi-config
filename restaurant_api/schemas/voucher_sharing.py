"""Pydantic v2 schemas for the voucher-sharing endpoints (模式 A / 模式 B).

Money fields use ``StrictDecimal`` (rejects ``float`` at the boundary) per the
project's no-float-money rule.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..models import VoucherGiftStatus, VoucherRewardKind


def _reject_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("float not allowed; use Decimal or string")
    return v


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


# ── 模式 A: gifts ─────────────────────────────────────────────────────────────


class GiftCreateRequest(BaseModel):
    """A member mints a shareable gift link. Reward defaults to the operator's
    configured amount; ``reward_amount`` is an optional override (e.g. campaigns)."""

    model_config = ConfigDict(frozen=True)

    sender_id: UUID
    reward_kind: VoucherRewardKind = VoucherRewardKind.POINTS
    reward_amount: StrictDecimal | None = Field(default=None, ge=0, le=1_000_000)


class GiftClaimRequest(BaseModel):
    """A friend claims a gift by its share token; they must already be a member
    (created on 加好友)."""

    model_config = ConfigDict(frozen=True)

    recipient_id: UUID


class VoucherGiftResponse(BaseModel):
    """A gift row projected back to HTTP."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sender_id: UUID
    recipient_id: UUID | None
    share_token: str
    status: VoucherGiftStatus
    reward_kind: VoucherRewardKind
    reward_amount: Decimal
    rewarded_at: datetime | None
    created_at: datetime


# ── 模式 B: grants ────────────────────────────────────────────────────────────


class GrantCreateRequest(BaseModel):
    """Operator hands a shareholder / 貴人 a capped allotment."""

    model_config = ConfigDict(frozen=True)

    grantee_name: str = Field(min_length=1, max_length=120)
    voucher_kind: str = Field(min_length=1, max_length=32)
    total_quota: int = Field(gt=0, le=100_000)
    valid_until: date
    face_value: StrictDecimal = Field(default=Decimal("0"), ge=0, le=1_000_000)
    menu_item_id: UUID | None = None
    grantee_id: UUID | None = None


class VoucherGrantResponse(BaseModel):
    """A grant (allotment) row projected back to HTTP — the operator's dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    grantee_name: str
    grantee_id: UUID | None
    voucher_kind: str
    face_value: Decimal
    menu_item_id: UUID | None
    total_quota: int
    used_count: int
    valid_until: date
    created_at: datetime


__all__ = [
    "GiftClaimRequest",
    "GiftCreateRequest",
    "GrantCreateRequest",
    "VoucherGiftResponse",
    "VoucherGrantResponse",
]
