"""Pydantic v2 schemas for POS session ordering + cash checkout (P1.3b / P1.4).

These sit on top of the table-session flow: a seated party's order is bound to
its ``table_session_id``. Lines are added one at a time from the POS (unlike
the bulk ingest ``OrderCreateRequest``), the unit price is snapshotted
server-side from the menu item, and checkout settles the bill in cash.
"""

from __future__ import annotations

from datetime import datetime
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
    modifier_option_ids: list[UUID] = Field(default_factory=list)
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


class InvoiceInfoFields(BaseModel):
    """發票資訊登錄 (🟡 partial) — 統編/載具 captured at checkout time.

    This does NOT issue a real 統一發票 (no MoF integration yet — see
    ``docs/22`` #20); it just records what the customer asked for onto the
    order so the real invoice module can pick it up later without a second
    round-trip. Same validation as the bulk-ingest ``OrderCreateRequest``.
    """

    model_config = ConfigDict(frozen=True)

    buyer_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    carrier_type: Literal["mobile", "citizen", "member", "paper"] | None = None
    carrier_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_mobile_carrier(self) -> InvoiceInfoFields:
        if self.carrier_type == "mobile":
            cid = self.carrier_id
            if cid is None or not cid.startswith("/") or len(cid) != 8:
                raise ValueError(
                    "mobile carrier_id must start with '/' and be exactly 8 chars"
                )
        return self


class CheckoutCashRequest(InvoiceInfoFields):
    """Settle the session's bill in cash. ``tendered`` must cover the total."""

    tendered: StrictDecimal = Field(ge=Decimal("0"))
    actor_id: UUID | None = None


class TakeoutItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    qty: StrictDecimal = Field(default=Decimal("1"), gt=Decimal("0"), le=Decimal("99"))
    notes: str | None = Field(default=None, max_length=200)


class TakeoutSaleRequest(InvoiceInfoFields):
    """外帶快速單 — one shot: build the order, take cash, close it. No table."""

    store_id: UUID
    items: list[TakeoutItem] = Field(min_length=1, max_length=50)
    tendered: StrictDecimal = Field(ge=Decimal("0"))
    actor_id: UUID | None = None


class OnlineTakeoutRequest(BaseModel):
    """線上外帶 — customer submits from their phone, no table, no login.

    Stays ``OPEN`` (no payment) until staff collects cash at pickup via
    ``collect_online_takeout`` — there's no payment gateway yet (🔴 in
    docs/22), so this is order-ahead, pay-at-counter.
    """

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    contact_name: str = Field(min_length=1, max_length=80)
    contact_phone: str = Field(min_length=1, max_length=32)
    items: list[TakeoutItem] = Field(min_length=1, max_length=50)
    pickup_at: datetime | None = None  # None = 盡快 (ASAP)
    notes: str | None = Field(default=None, max_length=200)


class OnlineTakeoutResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: OrderResponse
    estimated_total: Decimal


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
    "InvoiceInfoFields",
    "LineAddRequest",
    "LineUpdateRequest",
    "LineVoidRequest",
    "OnlineTakeoutRequest",
    "OnlineTakeoutResult",
    "PartialPayRequest",
    "PartialPayResult",
    "ServiceChargeRequest",
    "StrictDecimal",
    "TakeoutItem",
    "TakeoutSaleRequest",
]
