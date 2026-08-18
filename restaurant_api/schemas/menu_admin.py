"""Pydantic v2 schemas for the menu admin router (``/menu``).

Backing feature: 菜單後台管理 (docs/13_haodian_pos_comparison.md G1+G2) —
categories, sellable items, and reusable modifier groups (甜度/冰量/加料)
with a link table so a group is maintained once and attached to many items.

Conventions (project-wide):
- Input models are ``frozen=True``. We do NOT set ``strict=True`` (that
  would also block JSON string → UUID coercion); money fields use the
  ``StrictDecimal`` alias which 422s bare ``float`` literals.
- Response models use ``from_attributes=True`` so ORM rows validate directly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ..models.menu import ModifierSelectionType


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` literals on money fields.

    Strings, ints, and Decimals pass through (Pydantic coerces them
    exactly); floats carry binary rounding error and are refused.
    """
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]

# 台灣 11 大過敏原 codes (消保法) — kept in sync with models/menu.py comment.
ALLERGEN_CODES = frozenset(
    {
        "milk", "egg", "wheat", "peanut", "tree_nut", "shellfish",
        "fish", "soy", "sulfite", "sesame", "mango",
    }
)


def _check_allergens(values: list[str]) -> list[str]:
    unknown = [v for v in values if v not in ALLERGEN_CODES]
    if unknown:
        raise ValueError(f"unknown allergen codes: {unknown}; allowed: {sorted(ALLERGEN_CODES)}")
    return values


# ──────────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────────


class CategoryCreateRequest(BaseModel):
    """``POST /menu/categories``."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    store_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class CategoryPatchRequest(BaseModel):
    """``PATCH /menu/categories/{id}`` — only provided fields are applied."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None = None
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────────────────────────────────


class ItemCreateRequest(BaseModel):
    """``POST /menu/items``. SKU is unique per tenant among live rows."""

    model_config = ConfigDict(frozen=True)

    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    price: StrictDecimal = Field(ge=Decimal("0"))
    store_id: UUID | None = None
    category_id: UUID | None = None
    description: str | None = Field(default=None, max_length=1000)
    cost_estimate: StrictDecimal | None = Field(default=None, ge=Decimal("0"))
    allergens: list[str] = Field(default_factory=list)
    is_available: bool = True

    @model_validator(mode="after")
    def _validate_allergens(self) -> ItemCreateRequest:
        _check_allergens(self.allergens)
        return self


class ItemPatchRequest(BaseModel):
    """``PATCH /menu/items/{id}`` — partial update, unset fields untouched."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    price: StrictDecimal | None = Field(default=None, ge=Decimal("0"))
    category_id: UUID | None = None
    is_available: bool | None = None
    allergens: list[str] | None = None

    @model_validator(mode="after")
    def _validate_allergens(self) -> ItemPatchRequest:
        if self.allergens is not None:
            _check_allergens(self.allergens)
        return self


class ItemAvailabilityRequest(BaseModel):
    """``POST /menu/items/{id}/availability`` — 停售/恢復快捷開關."""

    model_config = ConfigDict(frozen=True)

    is_available: bool


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None = None
    category_id: UUID | None = None
    sku: str
    name: str
    description: str | None = None
    price: Decimal
    cost_estimate: Decimal | None = None
    allergens: list[str]
    is_available: bool
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Modifier groups + options
# ──────────────────────────────────────────────────────────────────────────


class ModifierOptionCreateRequest(BaseModel):
    """One option — nested inside group create, or ``POST .../options``."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    price_delta: StrictDecimal = Field(default=Decimal("0"))
    sort_order: int = Field(default=0, ge=0)
    is_available: bool = True


class ModifierOptionPatchRequest(BaseModel):
    """``PATCH /menu/modifier-options/{id}``."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    price_delta: StrictDecimal | None = None
    sort_order: int | None = Field(default=None, ge=0)
    is_available: bool | None = None


class ModifierGroupCreateRequest(BaseModel):
    """``POST /menu/modifier-groups`` — options may be created inline.

    Invariants (enforced here and re-checked in the service):
    - ``selection_type=single`` → ``max_select`` is treated as 1.
    - ``min_select <= max_select`` when ``max_select`` is not null.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    store_id: UUID | None = None
    selection_type: ModifierSelectionType = ModifierSelectionType.SINGLE
    is_required: bool = False
    min_select: int = Field(default=0, ge=0)
    max_select: int | None = Field(default=None, ge=1)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    options: list[ModifierOptionCreateRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_bounds(self) -> ModifierGroupCreateRequest:
        effective_max = 1 if self.selection_type is ModifierSelectionType.SINGLE else self.max_select
        if effective_max is not None and self.min_select > effective_max:
            raise ValueError(
                f"min_select ({self.min_select}) must be <= max_select ({effective_max})"
            )
        return self


class ModifierGroupPatchRequest(BaseModel):
    """``PATCH /menu/modifier-groups/{id}`` — bounds re-validated in service."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    selection_type: ModifierSelectionType | None = None
    is_required: bool | None = None
    min_select: int | None = Field(default=None, ge=0)
    max_select: int | None = Field(default=None, ge=1)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ModifierOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    group_id: UUID
    name: str
    price_delta: Decimal
    sort_order: int
    is_available: bool


class ModifierGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID | None = None
    name: str
    selection_type: ModifierSelectionType
    is_required: bool
    min_select: int
    max_select: int | None = None
    sort_order: int
    is_active: bool
    options: list[ModifierOptionResponse] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Item ↔ modifier-group links
# ──────────────────────────────────────────────────────────────────────────


class ItemModifierLinkRequest(BaseModel):
    """``POST /menu/items/{item_id}/modifier-groups``."""

    model_config = ConfigDict(frozen=True)

    modifier_group_id: UUID
    sort_order: int = Field(default=0, ge=0)


class ItemModifierLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    menu_item_id: UUID
    modifier_group_id: UUID
    sort_order: int


__all__ = [
    "ALLERGEN_CODES",
    "CategoryCreateRequest",
    "CategoryPatchRequest",
    "CategoryResponse",
    "ItemAvailabilityRequest",
    "ItemCreateRequest",
    "ItemModifierLinkRequest",
    "ItemModifierLinkResponse",
    "ItemPatchRequest",
    "ItemResponse",
    "ModifierGroupCreateRequest",
    "ModifierGroupPatchRequest",
    "ModifierGroupResponse",
    "ModifierOptionCreateRequest",
    "ModifierOptionPatchRequest",
    "ModifierOptionResponse",
    "StrictDecimal",
]
