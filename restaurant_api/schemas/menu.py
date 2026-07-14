"""Pydantic v2 schemas for the ``/menu`` router (P1.2).

Menu catalog CRUD — categories and sellable items. This is the master the
POS / kiosk / KDS all read from. Money fields (``price``, ``cost_estimate``)
use ``StrictDecimal`` so a ``float`` literal is 422'd before it can carry a
binary-rounding error into the ledger.

Deletes are soft (``deleted_at``): a deleted item/category disappears from
listings but its historical order lines still resolve.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` on money fields (see orders.py)."""
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


# ──────────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────────


class MenuCategoryCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    store_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class MenuCategoryUpdate(BaseModel):
    """Partial update — only provided fields change. All optional."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class MenuCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────────────────────────────────


_KitchenStationLiteral = Literal["kitchen", "bar", "dessert", "counter"]


class MenuItemCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    price: StrictDecimal = Field(ge=Decimal("0"))
    store_id: UUID | None = None
    category_id: UUID | None = None
    description: str | None = Field(default=None, max_length=1000)
    cost_estimate: StrictDecimal | None = Field(default=None, ge=Decimal("0"))
    allergens: list[str] = Field(default_factory=list)
    is_available: bool = True
    default_kitchen_station: _KitchenStationLiteral | None = None


class MenuItemUpdate(BaseModel):
    """Partial update — only provided fields change. All optional.

    ``category_id`` uses a sentinel-free approach: pass ``null`` to explicitly
    unset (uncategorise) the item; omit the key to leave it unchanged. The
    service distinguishes the two via the fields set on the model.
    """

    model_config = ConfigDict(frozen=True)

    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    price: StrictDecimal | None = Field(default=None, ge=Decimal("0"))
    category_id: UUID | None = None
    description: str | None = Field(default=None, max_length=1000)
    cost_estimate: StrictDecimal | None = Field(default=None, ge=Decimal("0"))
    allergens: list[str] | None = None
    is_available: bool | None = None
    default_kitchen_station: _KitchenStationLiteral | None = None


class MenuItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None
    category_id: UUID | None
    sku: str
    name: str
    description: str | None
    price: Decimal
    cost_estimate: Decimal | None
    allergens: list[str]
    is_available: bool
    default_kitchen_station: str | None = None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# 口味選項組/加價購 (modifier groups)
# ──────────────────────────────────────────────────────────────────────────


class ModifierOptionCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    price_delta: StrictDecimal = Decimal("0")
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class ModifierOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    group_id: UUID
    name: str
    price_delta: Decimal
    sort_order: int
    is_active: bool


class ModifierGroupCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    min_select: int = Field(default=0, ge=0)
    max_select: int = Field(default=1, ge=1)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    options: list[ModifierOptionCreate] = Field(default_factory=list)


class ModifierGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    menu_item_id: UUID
    name: str
    min_select: int
    max_select: int
    sort_order: int
    is_active: bool
    options: list[ModifierOptionResponse] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# 套餐組合 (combo components)
# ──────────────────────────────────────────────────────────────────────────


class ComboComponentCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    component_item_id: UUID
    qty: StrictDecimal = Field(default=Decimal("1"), gt=Decimal("0"))
    sort_order: int = Field(default=0, ge=0)


class ComboComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    combo_item_id: UUID
    component_item_id: UUID
    qty: Decimal
    sort_order: int


__all__ = [
    "ComboComponentCreate",
    "ComboComponentResponse",
    "MenuCategoryCreate",
    "MenuCategoryResponse",
    "MenuCategoryUpdate",
    "MenuItemCreate",
    "MenuItemResponse",
    "MenuItemUpdate",
    "ModifierGroupCreate",
    "ModifierGroupResponse",
    "ModifierOptionCreate",
    "ModifierOptionResponse",
    "StrictDecimal",
]
