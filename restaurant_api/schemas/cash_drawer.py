"""Pydantic v2 schemas for 交班/關帳 (cash drawer open/close, P7.2).

One row per open→close cycle of a register (``cash_drawer_sessions``, already
in the schema since Phase 1's closed-loop migration — this slice is the first
to actually drive it). ``expected_close`` and ``variance`` are computed
server-side from real cash-payment rows, never trusted from the client.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class DrawerOpenRequest(BaseModel):
    """開班 — start a till cycle with a known opening float (備用金)."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    opened_by: UUID
    register_no: str | None = Field(default=None, max_length=40)
    opening_float: StrictDecimal = Field(default=Decimal("0"), ge=Decimal("0"))


class DrawerCloseRequest(BaseModel):
    """交班/關帳 — count the drawer; server computes expected vs actual."""

    model_config = ConfigDict(frozen=True)

    closed_by: UUID
    closing_actual: StrictDecimal = Field(ge=Decimal("0"))
    notes: str | None = Field(default=None, max_length=500)


class DrawerExpectedBreakdown(BaseModel):
    """What the till *should* hold right now — the guided-closing checklist."""

    model_config = ConfigDict(frozen=True)

    opening_float: Decimal
    cash_sales: Decimal
    cash_refunds: Decimal
    expected_close: Decimal
    order_count: int


class CashDrawerSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    register_no: str | None
    opened_at: datetime
    closed_at: datetime | None
    opened_by: UUID
    closed_by: UUID | None
    opening_float: Decimal
    expected_close: Decimal | None
    closing_actual: Decimal | None
    variance: Decimal | None
    notes: str | None


class DrawerCloseResult(BaseModel):
    """Full daily-closing report handed back at 關帳 — what a new hire needs to
    see to trust the number without re-deriving it."""

    model_config = ConfigDict(frozen=True)

    session: CashDrawerSessionResponse
    expected: DrawerExpectedBreakdown
    variance: Decimal
    order_count: int
    gross_sales: Decimal
    discount_total: Decimal
    service_charge_total: Decimal
    net_sales: Decimal


__all__ = [
    "CashDrawerSessionResponse",
    "DrawerCloseRequest",
    "DrawerCloseResult",
    "DrawerExpectedBreakdown",
    "DrawerOpenRequest",
    "StrictDecimal",
]
