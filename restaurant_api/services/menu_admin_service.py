"""Menu admin business logic — categories / items / modifier groups.

Boundary rules (same as every service in the project):
- ``flush()`` only, never ``commit()`` (the DI layer owns the transaction).
- ``tenant_id`` is plumbed in by the router — services trust it.
- All errors raise ``DomainError`` subclasses (404 / 409 / 422).
- Money is always ``Decimal``.

Soft deletes: categories, items, and modifier groups stamp ``deleted_at``;
every read filters ``deleted_at IS NULL``. The tenant-scoped SKU unique
index is partial on live rows so a deleted item's SKU can be reused.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models.menu import (
    MenuCategory,
    MenuItem,
    MenuItemModifierGroup,
    ModifierGroup,
    ModifierOption,
    ModifierSelectionType,
)
from ..schemas.menu_admin import (
    CategoryCreateRequest,
    CategoryPatchRequest,
    CategoryResponse,
    ItemCreateRequest,
    ItemModifierLinkRequest,
    ItemModifierLinkResponse,
    ItemPatchRequest,
    ItemResponse,
    ModifierGroupCreateRequest,
    ModifierGroupPatchRequest,
    ModifierGroupResponse,
    ModifierOptionCreateRequest,
    ModifierOptionPatchRequest,
    ModifierOptionResponse,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


# ──────────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────────


async def create_category(
    session: AsyncSession,
    payload: CategoryCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> CategoryResponse:
    category = MenuCategory(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    session.add(category)
    await session.flush()
    logger.info("menu.category.create id=%s name=%s", category.id, category.name)
    return CategoryResponse.model_validate(category)


async def list_categories(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
) -> list[CategoryResponse]:
    stmt = (
        select(MenuCategory)
        .where(MenuCategory.tenant_id == tenant_id)
        .where(MenuCategory.deleted_at.is_(None))
        .order_by(MenuCategory.sort_order, MenuCategory.name)
    )
    if store_id is not None:
        stmt = stmt.where(MenuCategory.store_id == store_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [CategoryResponse.model_validate(c) for c in rows]


async def patch_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    payload: CategoryPatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> CategoryResponse:
    category = await _get_category(session, category_id, tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(category, field, value)
    await session.flush()
    await session.refresh(category)
    logger.info("menu.category.patch id=%s fields=%s", category.id, sorted(changes))
    return CategoryResponse.model_validate(category)


async def delete_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    category = await _get_category(session, category_id, tenant_id)
    category.deleted_at = _now()
    category.is_active = False
    await session.flush()
    logger.info("menu.category.delete id=%s", category.id)


# ──────────────────────────────────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────────────────────────────────


async def create_item(
    session: AsyncSession,
    payload: ItemCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> ItemResponse:
    await _ensure_sku_free(session, tenant_id, payload.sku)
    if payload.category_id is not None:
        await _get_category(session, payload.category_id, tenant_id)

    item = MenuItem(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        category_id=payload.category_id,
        sku=payload.sku,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        cost_estimate=payload.cost_estimate,
        allergens=list(payload.allergens),
        is_available=payload.is_available,
    )
    session.add(item)
    await session.flush()
    logger.info("menu.item.create id=%s sku=%s", item.id, item.sku)
    return ItemResponse.model_validate(item)


async def list_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    category_id: uuid.UUID | None = None,
    include_unavailable: bool = True,
) -> list[ItemResponse]:
    stmt = (
        select(MenuItem)
        .where(MenuItem.tenant_id == tenant_id)
        .where(MenuItem.deleted_at.is_(None))
        .order_by(MenuItem.name)
    )
    if category_id is not None:
        stmt = stmt.where(MenuItem.category_id == category_id)
    if not include_unavailable:
        stmt = stmt.where(MenuItem.is_available.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return [ItemResponse.model_validate(i) for i in rows]


async def patch_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    payload: ItemPatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> ItemResponse:
    item = await _get_item(session, item_id, tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    if "category_id" in changes and changes["category_id"] is not None:
        await _get_category(session, changes["category_id"], tenant_id)
    for field, value in changes.items():
        setattr(item, field, value)
    await session.flush()
    await session.refresh(item)
    logger.info("menu.item.patch id=%s fields=%s", item.id, sorted(changes))
    return ItemResponse.model_validate(item)


async def delete_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    item = await _get_item(session, item_id, tenant_id)
    item.deleted_at = _now()
    item.is_available = False
    await session.flush()
    logger.info("menu.item.delete id=%s sku=%s", item.id, item.sku)


async def set_item_availability(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    is_available: bool,
) -> ItemResponse:
    item = await _get_item(session, item_id, tenant_id)
    item.is_available = is_available
    await session.flush()
    await session.refresh(item)
    logger.info("menu.item.availability id=%s is_available=%s", item.id, is_available)
    return ItemResponse.model_validate(item)


# ──────────────────────────────────────────────────────────────────────────
# Modifier groups + options
# ──────────────────────────────────────────────────────────────────────────


def _check_group_bounds(
    selection_type: ModifierSelectionType,
    min_select: int,
    max_select: int | None,
) -> None:
    """selection_type=single → max_select is treated as 1; min<=max when bounded."""
    effective_max = 1 if selection_type is ModifierSelectionType.SINGLE else max_select
    if effective_max is not None and min_select > effective_max:
        raise ValidationError(
            message=f"min_select ({min_select}) must be <= max_select ({effective_max})",
            details={
                "min_select": min_select,
                "max_select": effective_max,
                "selection_type": selection_type.value,
            },
        )


async def create_modifier_group(
    session: AsyncSession,
    payload: ModifierGroupCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> ModifierGroupResponse:
    _check_group_bounds(payload.selection_type, payload.min_select, payload.max_select)
    max_select = (
        1
        if payload.selection_type is ModifierSelectionType.SINGLE
        else payload.max_select
    )
    group = ModifierGroup(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        selection_type=payload.selection_type,
        is_required=payload.is_required,
        min_select=payload.min_select,
        max_select=max_select,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    session.add(group)
    await session.flush()
    for opt in payload.options:
        session.add(
            ModifierOption(
                tenant_id=tenant_id,
                group_id=group.id,
                name=opt.name,
                price_delta=opt.price_delta,
                sort_order=opt.sort_order,
                is_available=opt.is_available,
            )
        )
    await session.flush()
    logger.info(
        "menu.modifier_group.create id=%s name=%s options=%d",
        group.id, group.name, len(payload.options),
    )
    return await _project_group(session, group.id, tenant_id)


async def list_modifier_groups(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
) -> list[ModifierGroupResponse]:
    stmt = (
        select(ModifierGroup)
        .options(selectinload(ModifierGroup.options))
        .where(ModifierGroup.tenant_id == tenant_id)
        .where(ModifierGroup.deleted_at.is_(None))
        .order_by(ModifierGroup.sort_order, ModifierGroup.name)
    )
    if store_id is not None:
        stmt = stmt.where(ModifierGroup.store_id == store_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [ModifierGroupResponse.model_validate(g) for g in rows]


async def patch_modifier_group(
    session: AsyncSession,
    group_id: uuid.UUID,
    payload: ModifierGroupPatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> ModifierGroupResponse:
    group = await _get_group(session, group_id, tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(group, field, value)
    # Re-validate the min/max invariant on the post-patch state.
    _check_group_bounds(group.selection_type, group.min_select, group.max_select)
    if group.selection_type is ModifierSelectionType.SINGLE:
        group.max_select = 1
    await session.flush()
    logger.info("menu.modifier_group.patch id=%s fields=%s", group.id, sorted(changes))
    return await _project_group(session, group.id, tenant_id)


async def delete_modifier_group(
    session: AsyncSession,
    group_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    group = await _get_group(session, group_id, tenant_id)
    group.deleted_at = _now()
    group.is_active = False
    await session.flush()
    logger.info("menu.modifier_group.delete id=%s", group.id)


async def create_modifier_option(
    session: AsyncSession,
    group_id: uuid.UUID,
    payload: ModifierOptionCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> ModifierOptionResponse:
    await _get_group(session, group_id, tenant_id)
    option = ModifierOption(
        tenant_id=tenant_id,
        group_id=group_id,
        name=payload.name,
        price_delta=payload.price_delta,
        sort_order=payload.sort_order,
        is_available=payload.is_available,
    )
    session.add(option)
    await session.flush()
    logger.info("menu.modifier_option.create id=%s group_id=%s", option.id, group_id)
    return ModifierOptionResponse.model_validate(option)


async def patch_modifier_option(
    session: AsyncSession,
    option_id: uuid.UUID,
    payload: ModifierOptionPatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> ModifierOptionResponse:
    stmt = (
        select(ModifierOption)
        .where(ModifierOption.id == option_id)
        .where(ModifierOption.tenant_id == tenant_id)
    )
    option = (await session.execute(stmt)).scalar_one_or_none()
    if option is None:
        raise NotFoundError(
            message=f"modifier option {option_id} not found",
            details={"option_id": str(option_id)},
        )
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(option, field, value)
    await session.flush()
    await session.refresh(option)
    logger.info("menu.modifier_option.patch id=%s fields=%s", option.id, sorted(changes))
    return ModifierOptionResponse.model_validate(option)


# ──────────────────────────────────────────────────────────────────────────
# Item ↔ modifier-group links
# ──────────────────────────────────────────────────────────────────────────


async def link_modifier_group(
    session: AsyncSession,
    item_id: uuid.UUID,
    payload: ItemModifierLinkRequest,
    *,
    tenant_id: uuid.UUID,
) -> ItemModifierLinkResponse:
    await _get_item(session, item_id, tenant_id)
    await _get_group(session, payload.modifier_group_id, tenant_id)

    existing = (
        await session.execute(
            select(MenuItemModifierGroup)
            .where(MenuItemModifierGroup.tenant_id == tenant_id)
            .where(MenuItemModifierGroup.menu_item_id == item_id)
            .where(MenuItemModifierGroup.modifier_group_id == payload.modifier_group_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            message="modifier group already linked to this item",
            details={
                "menu_item_id": str(item_id),
                "modifier_group_id": str(payload.modifier_group_id),
            },
        )

    link = MenuItemModifierGroup(
        tenant_id=tenant_id,
        menu_item_id=item_id,
        modifier_group_id=payload.modifier_group_id,
        sort_order=payload.sort_order,
    )
    session.add(link)
    await session.flush()
    logger.info(
        "menu.item_modifier.link item_id=%s group_id=%s",
        item_id, payload.modifier_group_id,
    )
    return ItemModifierLinkResponse.model_validate(link)


async def unlink_modifier_group(
    session: AsyncSession,
    item_id: uuid.UUID,
    group_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    link = (
        await session.execute(
            select(MenuItemModifierGroup)
            .where(MenuItemModifierGroup.tenant_id == tenant_id)
            .where(MenuItemModifierGroup.menu_item_id == item_id)
            .where(MenuItemModifierGroup.modifier_group_id == group_id)
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFoundError(
            message="modifier group is not linked to this item",
            details={"menu_item_id": str(item_id), "modifier_group_id": str(group_id)},
        )
    await session.delete(link)
    await session.flush()
    logger.info("menu.item_modifier.unlink item_id=%s group_id=%s", item_id, group_id)


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


async def _get_category(
    session: AsyncSession, category_id: uuid.UUID, tenant_id: uuid.UUID
) -> MenuCategory:
    stmt = (
        select(MenuCategory)
        .where(MenuCategory.id == category_id)
        .where(MenuCategory.tenant_id == tenant_id)
        .where(MenuCategory.deleted_at.is_(None))
    )
    category = (await session.execute(stmt)).scalar_one_or_none()
    if category is None:
        raise NotFoundError(
            message=f"menu category {category_id} not found",
            details={"category_id": str(category_id)},
        )
    return category


async def _get_item(
    session: AsyncSession, item_id: uuid.UUID, tenant_id: uuid.UUID
) -> MenuItem:
    stmt = (
        select(MenuItem)
        .where(MenuItem.id == item_id)
        .where(MenuItem.tenant_id == tenant_id)
        .where(MenuItem.deleted_at.is_(None))
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise NotFoundError(
            message=f"menu item {item_id} not found",
            details={"item_id": str(item_id)},
        )
    return item


async def _get_group(
    session: AsyncSession, group_id: uuid.UUID, tenant_id: uuid.UUID
) -> ModifierGroup:
    stmt = (
        select(ModifierGroup)
        .where(ModifierGroup.id == group_id)
        .where(ModifierGroup.tenant_id == tenant_id)
        .where(ModifierGroup.deleted_at.is_(None))
    )
    group = (await session.execute(stmt)).scalar_one_or_none()
    if group is None:
        raise NotFoundError(
            message=f"modifier group {group_id} not found",
            details={"group_id": str(group_id)},
        )
    return group


async def _ensure_sku_free(
    session: AsyncSession, tenant_id: uuid.UUID, sku: str
) -> None:
    """App-level guard mirroring the ``uq_menu_items_tenant_sku_live`` partial
    unique index — a clean 409 beats an IntegrityError-poisoned session."""
    stmt = (
        select(MenuItem.id)
        .where(MenuItem.tenant_id == tenant_id)
        .where(MenuItem.sku == sku)
        .where(MenuItem.deleted_at.is_(None))
        .limit(1)
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise ConflictError(
            message=f"SKU {sku!r} already exists for this tenant",
            details={"sku": sku},
        )


async def _project_group(
    session: AsyncSession, group_id: uuid.UUID, tenant_id: uuid.UUID
) -> ModifierGroupResponse:
    stmt = (
        select(ModifierGroup)
        .options(selectinload(ModifierGroup.options))
        .where(ModifierGroup.id == group_id)
        .where(ModifierGroup.tenant_id == tenant_id)
    )
    group = (await session.execute(stmt)).scalar_one()
    return ModifierGroupResponse.model_validate(group)


__all__ = [
    "create_category",
    "create_item",
    "create_modifier_group",
    "create_modifier_option",
    "delete_category",
    "delete_item",
    "delete_modifier_group",
    "link_modifier_group",
    "list_categories",
    "list_items",
    "list_modifier_groups",
    "patch_category",
    "patch_item",
    "patch_modifier_group",
    "patch_modifier_option",
    "set_item_availability",
    "unlink_modifier_group",
]
