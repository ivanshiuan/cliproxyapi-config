"""Menu catalog CRUD — categories + items.

Pure async service (flush-only, no commit — the ``get_db`` DI owns the
transaction). Deletes are soft. SKU uniqueness is enforced at the DB level
by the ``uq_menu_items_tenant_sku_live`` partial index; here we pre-check to
return a clean 409 instead of leaking an IntegrityError.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import MenuCategory, MenuItem
from ..schemas.menu import (
    MenuCategoryCreate,
    MenuCategoryResponse,
    MenuCategoryUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemUpdate,
)
from .audit_service import audit

# ──────────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────────


async def create_category(
    session: AsyncSession,
    payload: MenuCategoryCreate,
    *,
    tenant_id: uuid.UUID,
) -> MenuCategoryResponse:
    row = MenuCategory(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        name=payload.name,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="menu_category.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("menu_categories", row.id),
        after={"name": payload.name},
    )
    return MenuCategoryResponse.model_validate(row)


async def list_categories(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[MenuCategoryResponse]:
    stmt = select(MenuCategory).where(
        MenuCategory.tenant_id == tenant_id,
        MenuCategory.deleted_at.is_(None),
    )
    if store_id is not None:
        stmt = stmt.where(MenuCategory.store_id == store_id)
    if not include_inactive:
        stmt = stmt.where(MenuCategory.is_active.is_(True))
    stmt = stmt.order_by(MenuCategory.sort_order, MenuCategory.name)
    rows = (await session.execute(stmt)).scalars().all()
    return [MenuCategoryResponse.model_validate(r) for r in rows]


async def update_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    payload: MenuCategoryUpdate,
    *,
    tenant_id: uuid.UUID,
) -> MenuCategoryResponse:
    row = await _load_category(session, category_id, tenant_id)
    fields = payload.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="menu_category.updated",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("menu_categories", row.id),
        after=fields,
    )
    return MenuCategoryResponse.model_validate(row)


async def delete_category(
    session: AsyncSession,
    category_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    row = await _load_category(session, category_id, tenant_id)
    from datetime import UTC, datetime

    row.deleted_at = datetime.now(UTC)
    await session.flush()
    await audit(
        session,
        action="menu_category.deleted",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("menu_categories", row.id),
        before={"name": row.name},
    )


# ──────────────────────────────────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────────────────────────────────


async def create_item(
    session: AsyncSession,
    payload: MenuItemCreate,
    *,
    tenant_id: uuid.UUID,
) -> MenuItemResponse:
    await _guard_sku_unique(session, tenant_id=tenant_id, sku=payload.sku)
    if payload.category_id is not None:
        await _load_category(session, payload.category_id, tenant_id)
    row = MenuItem(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        category_id=payload.category_id,
        sku=payload.sku,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        cost_estimate=payload.cost_estimate,
        allergens=payload.allergens,
        is_available=payload.is_available,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="menu_item.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("menu_items", row.id),
        after={"sku": payload.sku, "name": payload.name, "price": str(payload.price)},
    )
    return MenuItemResponse.model_validate(row)


async def get_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> MenuItemResponse:
    row = await _load_item(session, item_id, tenant_id)
    return MenuItemResponse.model_validate(row)


async def list_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    available_only: bool = False,
    limit: int = 500,
) -> list[MenuItemResponse]:
    stmt = select(MenuItem).where(
        MenuItem.tenant_id == tenant_id,
        MenuItem.deleted_at.is_(None),
    )
    if store_id is not None:
        stmt = stmt.where(MenuItem.store_id == store_id)
    if category_id is not None:
        stmt = stmt.where(MenuItem.category_id == category_id)
    if available_only:
        stmt = stmt.where(MenuItem.is_available.is_(True))
    stmt = stmt.order_by(MenuItem.name).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [MenuItemResponse.model_validate(r) for r in rows]


async def update_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    payload: MenuItemUpdate,
    *,
    tenant_id: uuid.UUID,
) -> MenuItemResponse:
    row = await _load_item(session, item_id, tenant_id)
    fields = payload.model_dump(exclude_unset=True)
    if "sku" in fields and fields["sku"] != row.sku:
        await _guard_sku_unique(session, tenant_id=tenant_id, sku=fields["sku"])
    if "category_id" in fields and fields["category_id"] is not None:
        await _load_category(session, fields["category_id"], tenant_id)
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="menu_item.updated",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("menu_items", row.id),
        after={k: (str(v) if k in ("price", "cost_estimate") else v) for k, v in fields.items()},
    )
    return MenuItemResponse.model_validate(row)


async def delete_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    row = await _load_item(session, item_id, tenant_id)
    from datetime import UTC, datetime

    row.deleted_at = datetime.now(UTC)
    await session.flush()
    await audit(
        session,
        action="menu_item.deleted",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("menu_items", row.id),
        before={"sku": row.sku, "name": row.name},
    )


# ──────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────


async def _load_category(
    session: AsyncSession, category_id: uuid.UUID, tenant_id: uuid.UUID
) -> MenuCategory:
    row = (
        await session.execute(
            select(MenuCategory).where(
                MenuCategory.id == category_id,
                MenuCategory.tenant_id == tenant_id,
                MenuCategory.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"menu category {category_id} not found",
            details={"category_id": str(category_id)},
        )
    return row


async def _load_item(
    session: AsyncSession, item_id: uuid.UUID, tenant_id: uuid.UUID
) -> MenuItem:
    row = (
        await session.execute(
            select(MenuItem).where(
                MenuItem.id == item_id,
                MenuItem.tenant_id == tenant_id,
                MenuItem.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"menu item {item_id} not found",
            details={"item_id": str(item_id)},
        )
    return row


async def _guard_sku_unique(
    session: AsyncSession, *, tenant_id: uuid.UUID, sku: str
) -> None:
    exists = (
        await session.execute(
            select(MenuItem.id).where(
                MenuItem.tenant_id == tenant_id,
                MenuItem.sku == sku,
                MenuItem.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise ConflictError(
            message=f"menu item SKU {sku!r} already exists",
            details={"sku": sku},
        )


__all__ = [
    "create_category",
    "create_item",
    "delete_category",
    "delete_item",
    "get_item",
    "list_categories",
    "list_items",
    "update_category",
    "update_item",
]
