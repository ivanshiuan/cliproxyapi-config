"""Pydantic v2 schemas for the ``/discount-rules`` router (自動折扣規則, #10).

A rule = condition (滿額 min_subtotal and/or 每日時段 start/end) + discount
(percent 0..1 or TWD amount). Money fields use ``StrictDecimal`` so a float
literal is 422'd before it can carry a binary-rounding error into pricing.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` on money fields (see orders.py)."""
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class DiscountRuleCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    kind: Literal["percent", "amount"]
    value: StrictDecimal = Field(gt=Decimal("0"))
    store_id: UUID | None = None
    # 滿額門檻 — rule applies when gross >= this. None = no amount condition.
    min_subtotal: StrictDecimal | None = Field(default=None, gt=Decimal("0"))
    # 每日時段 (Asia/Taipei); both-or-neither. start > end wraps past midnight.
    start_time: time | None = None
    end_time: time | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self) -> DiscountRuleCreate:
        if self.kind == "percent" and not (Decimal("0") < self.value <= Decimal("1")):
            raise ValueError("percent 折扣的 value 必須在 0 到 1 之間 (0.1 = 九折)")
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time 與 end_time 必須同時設定或同時留空")
        if self.start_time is not None and self.start_time == self.end_time:
            raise ValueError("start_time 不能等於 end_time (區間長度為零)")
        return self


class DiscountRuleUpdate(BaseModel):
    """Partial update — v1 only supports pausing/resuming a rule. Changing a
    rule's economics mid-flight is a new rule, not an edit (audit clarity)."""

    model_config = ConfigDict(frozen=True)

    is_active: bool


class DiscountRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    name: str
    kind: str
    value: Decimal
    min_subtotal: Decimal | None
    start_time: time | None
    end_time: time | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


__all__ = [
    "DiscountRuleCreate",
    "DiscountRuleResponse",
    "DiscountRuleUpdate",
    "StrictDecimal",
]
