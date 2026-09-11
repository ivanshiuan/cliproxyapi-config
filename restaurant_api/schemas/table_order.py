"""Pydantic v2 schemas for customer table-QR ordering (``/t/{qr_token}``, P2).

Customer-facing: everything here is safe to show a guest's phone. The menu shape
deliberately excludes ``cost_estimate`` / SKU / any operator field — leaking
ingredient costs to a table QR would be a real business fumble.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class PublicMenuItem(BaseModel):
    """One orderable item as shown to the guest. No costs, no SKUs."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    price: Decimal
    allergens: list[str] | None = None


class TableContext(BaseModel):
    """Everything the guest page needs on load: where am I, can I order, menu."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    zone: str | None
    store_id: UUID
    is_open: bool
    menu: list[PublicMenuItem]


class CartItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    qty: StrictDecimal = Field(default=Decimal("1"), gt=Decimal("0"), le=Decimal("99"))
    notes: str | None = Field(default=None, max_length=200)


class CartSubmit(BaseModel):
    """The guest's cart — one submit appends every item to the table's bill."""

    model_config = ConfigDict(frozen=True)

    items: list[CartItem] = Field(min_length=1, max_length=50)


class GuestLine(BaseModel):
    """One line of the guest-visible bill."""

    model_config = ConfigDict(frozen=True)

    name: str | None
    qty: Decimal
    line_total: Decimal


class GuestBill(BaseModel):
    """The guest-visible bill: lines + total. No internal ids beyond what the
    page needs, no payment/audit detail."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    lines: list[GuestLine]
    total: Decimal


__all__ = [
    "CartItem",
    "CartSubmit",
    "GuestBill",
    "GuestLine",
    "PublicMenuItem",
    "TableContext",
]
