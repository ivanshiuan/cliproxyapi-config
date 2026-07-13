"""Pydantic v2 schemas for POS session ordering + cash checkout (P1.3b / P1.4).

These sit on top of the table-session flow: a seated party's order is bound to
its ``table_session_id``. Lines are added one at a time from the POS (unlike
the bulk ingest ``OrderCreateRequest``), the unit price is snapshotted
server-side from the menu item, and checkout settles the bill in cash.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .orders import OrderResponse
from .pos_auth import ManagerOverride


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class LineAddRequest(BaseModel):
    """Add one item to the session's open order. Price is snapshotted from the
    menu item server-side — the POS never sends the price."""

    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    qty: StrictDecimal = Field(default=Decimal("1"), gt=Decimal("0"))
    notes: str | None = Field(default=None, max_length=200)
    kitchen_station: Literal["kitchen", "bar", "dessert", "counter"] | None = None
    actor_id: UUID | None = None


class LineUpdateRequest(BaseModel):
    """Change a line's quantity (改量). line_total is recomputed server-side."""

    model_config = ConfigDict(frozen=True)

    qty: StrictDecimal = Field(gt=Decimal("0"))
    actor_id: UUID | None = None


class LineVoidRequest(BaseModel):
    """退菜 — remove a line and reverse its stock consumption. Audited.

    Sensitive: the actor's role must hold VOID_LINE, or a manager authorizes
    inline via ``override``.
    """

    model_config = ConfigDict(frozen=True)

    reason: str | None = Field(default=None, max_length=200)
    actor_id: UUID | None = None
    override: ManagerOverride | None = None


class DiscountApplyRequest(BaseModel):
    """折扣 — apply a percent/amount discount to the session's open order.

    Sensitive: the actor's role must hold APPLY_DISCOUNT, or a manager
    authorizes inline via ``override``. The discount flows into checkout's
    net-revenue calc automatically (same discount stack the till uses).
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["percent", "amount", "comp"]
    value: StrictDecimal = Field(ge=Decimal("0"))
    reason: str | None = Field(default=None, max_length=200)
    override: ManagerOverride | None = None

    @model_validator(mode="after")
    def _check_range(self) -> DiscountApplyRequest:
        # percent is a fraction (0.05 = 5%); amount is TWD off the subtotal.
        if self.kind == "percent" and not (Decimal("0") <= self.value <= Decimal("1")):
            raise ValueError("percent discount must be between 0 and 1")
        return self


class CheckoutCashRequest(BaseModel):
    """Settle the session's bill in cash. ``tendered`` must cover the total."""

    model_config = ConfigDict(frozen=True)

    tendered: StrictDecimal = Field(ge=Decimal("0"))
    actor_id: UUID | None = None


class TakeoutItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    qty: StrictDecimal = Field(default=Decimal("1"), gt=Decimal("0"), le=Decimal("99"))
    notes: str | None = Field(default=None, max_length=200)


class TakeoutSaleRequest(BaseModel):
    """外帶快速單 — one shot: build the order, take cash, close it. No table."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    items: list[TakeoutItem] = Field(min_length=1, max_length=50)
    tendered: StrictDecimal = Field(ge=Decimal("0"))
    actor_id: UUID | None = None


class CheckoutQuote(BaseModel):
    """Amount due for the session's open order — the single source of truth the
    checkout UI shows (no client-side money math). ``net`` includes the service
    charge; ``remaining`` accounts for partial payments already taken (拆單)."""

    model_config = ConfigDict(frozen=True)

    gross: Decimal
    discount_total: Decimal
    service_charge: Decimal
    net: Decimal
    paid: Decimal
    remaining: Decimal


class ServiceChargeRequest(BaseModel):
    """Set the order's 服務費 rate (0 removes it; 0.1 = 10%)."""

    model_config = ConfigDict(frozen=True)

    rate: StrictDecimal = Field(ge=Decimal("0"), le=Decimal("0.5"))
    actor_id: UUID | None = None


class PartialPayRequest(BaseModel):
    """拆單/分開結帳 — settle part of the bill in cash. ``amount`` is the share
    being paid now; ``tendered`` is the cash handed over (change returned)."""

    model_config = ConfigDict(frozen=True)

    amount: StrictDecimal = Field(gt=Decimal("0"))
    tendered: StrictDecimal = Field(gt=Decimal("0"))
    actor_id: UUID | None = None


class PartialPayResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    paid_amount: Decimal
    change: Decimal
    remaining: Decimal
    closed: bool  # True when the bill is settled → order + seating closed


class CheckoutResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: OrderResponse
    total: Decimal
    tendered: Decimal
    change: Decimal


__all__ = [
    "CheckoutCashRequest",
    "CheckoutQuote",
    "CheckoutResult",
    "DiscountApplyRequest",
    "LineAddRequest",
    "LineUpdateRequest",
    "LineVoidRequest",
    "PartialPayRequest",
    "PartialPayResult",
    "ServiceChargeRequest",
    "StrictDecimal",
    "TakeoutItem",
    "TakeoutSaleRequest",
]
