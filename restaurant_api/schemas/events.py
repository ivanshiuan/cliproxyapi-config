"""Pydantic v2 schemas for the ``/events`` router (waste / staff_meal / tasting).

Conventions
-----------
- Inputs are ``frozen=True``. We deliberately do **not** put ``strict=True``
  on the model itself — that would reject JSON-string forms of UUID / date
  which are the canonical wire shapes for HTTP. Instead we put ``strict=True``
  on every Decimal field via ``StrictDecimal``, which rejects bare ``float``
  literals (the monetary footgun) while still parsing ``"12.3456"`` correctly.
  Same pattern as ``schemas/orders.py``.
- All money / quantity values are ``Decimal``; we never funnel through float.
- Responses use ``from_attributes=True`` so the service can ``model_validate``
  ORM rows directly.
- Datetimes are timezone-aware; tz-naive payloads are rejected (cross-field
  validator) so the ledger never gets ambiguous "wall clock" entries.

Idempotency
-----------
Each create-request carries an optional ``external_ref``. Re-posting with the
same ``external_ref`` for the same ``(store_id, event_table)`` returns the
existing event row (HTTP 200) instead of writing a duplicate. The token is
echoed back via ``stock_movements.note`` so duplicate detection works without
adding a column to the event tables — see ``services/events_service.py``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _reject_float(value: Any) -> Any:
    """Reject ``float`` inputs before Pydantic coerces them into a Decimal.

    Pydantic's ``strict=True`` on a Decimal field rejects strings too, which
    breaks JSON-over-HTTP where Decimals are canonically sent as strings.
    This pre-validator gives us the property we actually want: ``float``
    → 422, anything else (str / int / Decimal) flows on to normal Decimal
    coercion.
    """
    if isinstance(value, float):
        raise ValueError("float is not allowed for Decimal fields; send as string")
    return value


# Float-rejecting Decimal alias. Use this on every money / qty field — never
# the bare ``Decimal`` — so callers can't sneak in a binary fraction that
# silently rounds wrong against the DB's ``Numeric(14, 4)``.
StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]

# ──────────────────────────────────────────────────────────────────────────
# Requests
# ──────────────────────────────────────────────────────────────────────────


class WasteEventCreate(BaseModel):
    """Record a 報廢 event: ingredient went bad / dropped / over-prepped."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    ingredient_id: UUID
    qty: StrictDecimal = Field(gt=Decimal("0"))
    cost: StrictDecimal = Field(ge=Decimal("0"))
    reason: str = Field(min_length=1, max_length=500)
    reported_by: UUID | None = None
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _occurred_at_tz_aware(self) -> WasteEventCreate:
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class StaffMealEventCreate(BaseModel):
    """Record an 員工餐: prepared menu item OR ad-hoc ingredient bundle."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    employee_id: UUID
    menu_item_id: UUID | None = None
    ingredients_used: dict[UUID, StrictDecimal] | None = None
    total_cost: StrictDecimal = Field(ge=Decimal("0"))
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> StaffMealEventCreate:
        has_menu = self.menu_item_id is not None
        has_bundle = self.ingredients_used is not None and len(self.ingredients_used) > 0
        if has_menu == has_bundle:
            raise ValueError("exactly one of menu_item_id, ingredients_used must be set")
        if has_bundle:
            assert self.ingredients_used is not None  # mypy/pyright narrowing
            for ing_id, qty in self.ingredients_used.items():
                if qty <= Decimal("0"):
                    raise ValueError(f"ingredients_used[{ing_id}] must be > 0")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class TastingEventCreate(BaseModel):
    """Record a 試吃 / 試菜 event sourced from a menu item OR a single ingredient."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    menu_item_id: UUID | None = None
    ingredient_id: UUID | None = None
    qty: StrictDecimal = Field(gt=Decimal("0"))
    total_cost: StrictDecimal = Field(ge=Decimal("0"))
    purpose: str = Field(min_length=1, max_length=200)
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> TastingEventCreate:
        if (self.menu_item_id is None) == (self.ingredient_id is None):
            raise ValueError("exactly one of menu_item_id, ingredient_id must be set")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


# ──────────────────────────────────────────────────────────────────────────
# Responses
# ──────────────────────────────────────────────────────────────────────────


EventTypeLiteral = Literal["waste", "staff_meal", "tasting"]


class WasteEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: Literal["waste"] = "waste"
    store_id: UUID
    ingredient_id: UUID
    qty: Decimal
    cost: Decimal
    reason: str
    reported_by: UUID | None
    occurred_at: datetime
    movement_id: UUID | None = None


class StaffMealEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: Literal["staff_meal"] = "staff_meal"
    store_id: UUID
    employee_id: UUID
    menu_item_id: UUID | None
    ingredients_used: dict[UUID, Decimal] | None
    total_cost: Decimal
    occurred_at: datetime
    movements_created: int


class TastingEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: Literal["tasting"] = "tasting"
    store_id: UUID
    menu_item_id: UUID | None
    ingredient_id: UUID | None
    qty: Decimal
    total_cost: Decimal
    purpose: str
    occurred_at: datetime
    movements_created: int


# Discriminated union for GET /events list responses.
EventListItem = Annotated[
    WasteEventResponse | StaffMealEventResponse | TastingEventResponse,
    Field(discriminator="event_type"),
]


__all__ = [
    "EventListItem",
    "EventTypeLiteral",
    "StaffMealEventCreate",
    "StaffMealEventResponse",
    "TastingEventCreate",
    "TastingEventResponse",
    "WasteEventCreate",
    "WasteEventResponse",
]
