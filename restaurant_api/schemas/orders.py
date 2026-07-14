"""Pydantic v2 schemas for the Orders router.

Contract reference: ``specs/orders_router.md``.

Conventions:
- Input models are ``frozen=True``. We do NOT set ``strict=True`` on the
  whole model because JSON-over-HTTP needs string → UUID / string → date
  coercion. Instead we use ``StrictDecimal`` (an ``Annotated`` alias) on
  every money field — Pydantic then rejects bare ``float`` literals there
  while still parsing ``"12.3456"`` as a Decimal.
- Response models use ``from_attributes=True`` so SQLAlchemy rows can be
  fed straight into ``OrderResponse.model_validate(...)``.
- The Asia/Taipei zoneing of timestamp fields is a presentation concern
  handled in the service layer (we store UTC, render TPE via
  ``astimezone(ZoneInfo("Asia/Taipei"))``).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` literals on money fields.

    We can't use ``strict=True`` because that also blocks the canonical
    ``"12.3456"`` → ``Decimal`` path that JSON-over-HTTP relies on. This
    custom check lets strings, ints, and Decimals through but rejects
    floats (which carry binary rounding errors and have no place in money).
    """
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


# Money type alias — strings parse via Pydantic's default, floats are
# 422'd by the pre-validator, ints / Decimals pass through unchanged.
StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]

# ──────────────────────────────────────────────────────────────────────────
# Request schemas
# ──────────────────────────────────────────────────────────────────────────


class OrderLineCreate(BaseModel):
    """One line on a new order. ``unit_price`` is a sale-time snapshot.

    ``kitchen_station`` opts this line into the KDS (Kitchen Display System).
    When set, the orders service stamps ``kitchen_status=queued`` +
    ``sent_to_kitchen_at=now()`` so the line shows up on the kitchen
    display the moment the order saves. Leave it ``None`` for counter-only
    lines (no kitchen prep needed — drinks pre-poured, retail items, etc.).
    """

    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    qty: StrictDecimal = Field(gt=Decimal("0"))
    unit_price: StrictDecimal = Field(ge=Decimal("0"))
    notes: str | None = Field(default=None, max_length=200)
    kitchen_station: Literal["kitchen", "bar", "dessert", "counter"] | None = None


class OrderDiscountCreate(BaseModel):
    """Discount / comp / allowance applied to the whole order.

    ``value`` semantics depends on ``kind``:
    - ``percent`` → 0..1 fraction (e.g. ``0.10`` for 10% off)
    - others → TWD amount, must be ``>= 0``
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["percent", "amount", "comp", "allowance", "employee"]
    value: StrictDecimal
    reason: str | None = Field(default=None, max_length=200)
    applied_by: UUID | None = None

    @model_validator(mode="after")
    def _check_value_range(self) -> OrderDiscountCreate:
        if self.kind == "percent":
            if self.value < Decimal("0") or self.value > Decimal("1"):
                raise ValueError("percent discount value must be between 0 and 1")
        else:
            if self.value < Decimal("0"):
                raise ValueError(f"{self.kind} discount value must be >= 0")
        return self


class OrderPaymentCreate(BaseModel):
    """A tender (cash, card, LINE Pay, …) attached to the order."""

    model_config = ConfigDict(frozen=True)

    method: Literal[
        "cash", "credit", "linepay", "applepay", "jko",
        "ubereats", "foodpanda", "voucher", "other",
    ]
    amount: StrictDecimal = Field(ge=Decimal("0"))
    fee_amount: StrictDecimal = Field(default=Decimal("0"), ge=Decimal("0"))
    reference: str | None = Field(default=None, max_length=100)
    paid_at: datetime | None = None

    @field_validator("paid_at")
    @classmethod
    def _require_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("paid_at must be timezone-aware")
        return v


class OrderCreateRequest(BaseModel):
    """``POST /orders`` request body.

    Carries the 統一發票 / 載具 / 統編 fields so a Taiwan invoice can be
    issued downstream without a second round-trip. ``external_pos_id`` is
    the idempotency key when present.
    """

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    # Optional FK to a known customer (LINE / phone / member scan). When
    # present, the close-order step writes a points-accrual ledger row
    # (and pushes a LINE confirmation when the customer has line_user_id).
    customer_id: UUID | None = None
    order_no: str = Field(min_length=1, max_length=64)
    business_date: date
    opened_at: datetime | None = None
    external_pos_id: str | None = Field(default=None, min_length=1, max_length=64)
    pos_source: Literal["ichef", "posplus", "manual"] = "manual"
    invoice_number: str | None = Field(default=None, pattern=r"^[A-Z]{2}[0-9]{8}$")
    carrier_type: Literal["mobile", "citizen", "member", "paper"] | None = None
    carrier_id: str | None = Field(default=None, max_length=64)
    buyer_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    notes: str | None = Field(default=None, max_length=1000)
    lines: list[OrderLineCreate] = Field(default_factory=list)
    discounts: list[OrderDiscountCreate] = Field(default_factory=list)
    payments: list[OrderPaymentCreate] = Field(default_factory=list)

    @field_validator("opened_at")
    @classmethod
    def _opened_at_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _check_mobile_carrier(self) -> OrderCreateRequest:
        if self.carrier_type == "mobile":
            cid = self.carrier_id
            if cid is None or not cid.startswith("/") or len(cid) != 8:
                raise ValueError(
                    "mobile carrier_id must start with '/' and be exactly 8 chars",
                )
        return self


class OrderCloseRequest(BaseModel):
    """``POST /orders/{id}/close`` request body. Default ``closed_at`` is now()."""

    model_config = ConfigDict(frozen=True)

    closed_at: datetime | None = None

    @field_validator("closed_at")
    @classmethod
    def _tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("closed_at must be timezone-aware")
        return v


class OrderVoidRequest(BaseModel):
    """``POST /orders/{id}/void`` request body. ``reason`` is mandatory."""

    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=1, max_length=200)


# ──────────────────────────────────────────────────────────────────────────
# Response schemas
# ──────────────────────────────────────────────────────────────────────────


class OrderLineModifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    option_name: str
    price_delta: Decimal


class OrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    menu_item_id: UUID
    # 套餐組合: set when this line is an auto-expanded $0 component ticket of
    # a combo item's line (see pos_order_service._expand_combo).
    combo_parent_id: UUID | None = None
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal
    cogs_actual: Decimal | None = None
    cogs_theoretical: Decimal | None = None
    notes: str | None = None
    # KDS fields — None when the line wasn't routed to a kitchen station.
    kitchen_station: str | None = None
    kitchen_status: str | None = None
    sent_to_kitchen_at: datetime | None = None
    modifiers: list[OrderLineModifierResponse] = Field(default_factory=list)


class OrderDiscountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    value: Decimal
    reason: str | None = None
    applied_by: UUID | None = None


class OrderPaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    method: str
    amount: Decimal
    fee_amount: Decimal
    reference: str | None = None
    paid_at: datetime  # rendered Asia/Taipei in the service layer


class OrderResponse(BaseModel):
    """``GET /orders/{id}`` and POST returns. Timestamps in Asia/Taipei."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    tenant_id: UUID
    customer_id: UUID | None = None
    order_no: str
    business_date: date
    opened_at: datetime
    closed_at: datetime | None = None
    status: Literal["open", "closed", "voided", "refunded"]
    # POS provenance (P1/P2): how the order was placed and which seating owns it.
    order_type: Literal["dine_in", "takeout", "delivery"] | None = None
    channel: Literal["pos", "table_qr", "tablet", "online", "external"] | None = None
    table_session_id: UUID | None = None
    invoice_number: str | None = None
    carrier_type: str | None = None
    carrier_id: str | None = None
    buyer_tax_id: str | None = None
    external_pos_id: str | None = None
    pos_source: str | None = None
    notes: str | None = None
    lines: list[OrderLineResponse] = Field(default_factory=list)
    discounts: list[OrderDiscountResponse] = Field(default_factory=list)
    payments: list[OrderPaymentResponse] = Field(default_factory=list)


__all__ = [
    "OrderCloseRequest",
    "OrderCreateRequest",
    "OrderDiscountCreate",
    "OrderDiscountResponse",
    "OrderLineCreate",
    "OrderLineModifierResponse",
    "OrderLineResponse",
    "OrderPaymentCreate",
    "OrderPaymentResponse",
    "OrderResponse",
    "OrderVoidRequest",
]
