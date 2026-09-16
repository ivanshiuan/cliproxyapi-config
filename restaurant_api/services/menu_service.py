"""Menu catalog CRUD — categories + items.

Pure async service (flush-only, no commit — the ``get_db`` DI owns the
transaction). Deletes are soft. SKU uniqueness is enforced at the DB level
by the ``uq_menu_items_tenant_sku_live`` partial index; here we pre-check to
return a clean 409 instead of leaking an IntegrityError.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import (
    ComboComponent,
    MenuCategory,
    MenuItem,
    ModifierGroup,
    ModifierOption,
    Order,
    OrderLine,
    OrderStatus,
)
from ..schemas.menu import (
    ComboComponentCreate,
    ComboComponentResponse,
    MenuCategoryCreate,
    MenuCategoryResponse,
    MenuCategoryUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemTranslationUpdate,
    MenuItemUpdate,
    ModifierGroupCreate,
    ModifierGroupResponse,
    RecommendedItem,
)
from .audit_service import audit

_TPE = ZoneInfo("Asia/Taipei")


def is_item_available_now(item: MenuItem, now: time | None = None) -> bool:
    """時段菜單: is ``item`` orderable at ``now`` (Asia/Taipei wall clock)?

    Both window fields NULL = always available. ``start > end`` wraps past
    midnight (e.g. 宵夜 21:00-02:00 is available at 23:00 and at 01:00).
    """
    start, end = item.available_start_time, item.available_end_time
    if start is None or end is None:
        return True
    if now is None:
        now = datetime.now(_TPE).time()
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _guard_availability_window(start: time | None, end: time | None) -> None:
    if (start is None) != (end is None):
        raise ValidationError("available_start_time 與 available_end_time 必須同時設定或同時留空")
    if start is not None and end is not None and start == end:
        raise ValidationError("available_start_time 不能等於 available_end_time (區間長度為零)")

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
    sku = payload.sku or uuid.uuid4().hex[:8].upper()
    await _guard_sku_unique(session, tenant_id=tenant_id, sku=sku)
    if payload.category_id is not None:
        await _load_category(session, payload.category_id, tenant_id)
    _guard_availability_window(payload.available_start_time, payload.available_end_time)
    row = MenuItem(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        category_id=payload.category_id,
        sku=sku,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        cost_estimate=payload.cost_estimate,
        allergens=payload.allergens,
        is_available=payload.is_available,
        default_kitchen_station=payload.default_kitchen_station,
        available_start_time=payload.available_start_time,
        available_end_time=payload.available_end_time,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="menu_item.created",
        tenant_id=tenant_id,
        store_id=payload.store_id,
        target=("menu_items", row.id),
        after={"sku": sku, "name": payload.name, "price": str(payload.price)},
    )
    return MenuItemResponse.model_validate(row)


def _localize(resp: MenuItemResponse, translations: dict[str, dict[str, str]], lang: str | None) -> MenuItemResponse:
    """多語系菜單: overlay name/description from ``translations[lang]``.

    Missing language or missing field silently falls back to the zh-Hant
    original — a customer never sees a blank name because a translator
    hasn't gotten to a field yet.
    """
    if not lang:
        return resp
    tr = translations.get(lang)
    if not tr:
        return resp
    if tr.get("name"):
        resp.name = tr["name"]
    if tr.get("description"):
        resp.description = tr["description"]
    return resp


async def get_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    lang: str | None = None,
) -> MenuItemResponse:
    row = await _load_item(session, item_id, tenant_id)
    return _localize(MenuItemResponse.model_validate(row), row.translations, lang)


async def list_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    available_only: bool = False,
    available_now: bool = False,
    lang: str | None = None,
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
    # 時段菜單: the wrap-around window (start > end) isn't a plain SQL BETWEEN,
    # so filter in Python — menu lists are small, this is cheap.
    if available_now:
        now = datetime.now(_TPE).time()
        rows = [r for r in rows if is_item_available_now(r, now)]
    return [_localize(MenuItemResponse.model_validate(r), r.translations, lang) for r in rows]


async def set_translation(
    session: AsyncSession,
    item_id: uuid.UUID,
    payload: MenuItemTranslationUpdate,
    *,
    tenant_id: uuid.UUID,
) -> MenuItemResponse:
    """多語系菜單: set (or overwrite) one language's translation."""
    row = await _load_item(session, item_id, tenant_id)
    updated = dict(row.translations)
    entry: dict[str, str] = {"name": payload.name}
    if payload.description:
        entry["description"] = payload.description
    updated[payload.lang] = entry
    row.translations = updated  # reassign (not mutate in place) so the ORM sees the change
    await session.flush()
    await session.refresh(row)
    await audit(
        session,
        action="menu_item.translation_set",
        tenant_id=tenant_id,
        store_id=row.store_id,
        target=("menu_items", row.id),
        after={"lang": payload.lang, "name": payload.name},
    )
    return MenuItemResponse.model_validate(row)


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
    if "available_start_time" in fields or "available_end_time" in fields:
        _guard_availability_window(row.available_start_time, row.available_end_time)
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
# 規則版智慧加購推薦 (#21/#25) — 先出規則版, 之後再上 AI 個人化版本。
# ──────────────────────────────────────────────────────────────────────────


async def _co_occurring_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    cart_ids: set[uuid.UUID],
    exclude: set[uuid.UUID],
    limit: int,
) -> list[uuid.UUID]:
    """購物車搭配建議: items most often CLOSED-ordered alongside ``cart_ids``."""
    anchor_orders = (
        select(OrderLine.order_id)
        .join(Order, OrderLine.order_id == Order.id)
        .where(
            Order.tenant_id == tenant_id,
            Order.store_id == store_id,
            Order.status == OrderStatus.CLOSED,
            Order.deleted_at.is_(None),
            OrderLine.menu_item_id.in_(cart_ids),
        )
        .distinct()
    )
    stmt = select(
        OrderLine.menu_item_id, func.count(func.distinct(OrderLine.order_id)).label("cnt")
    ).where(OrderLine.order_id.in_(anchor_orders))
    if exclude:
        stmt = stmt.where(OrderLine.menu_item_id.notin_(exclude))
    stmt = stmt.group_by(OrderLine.menu_item_id).order_by(func.count(func.distinct(OrderLine.order_id)).desc()).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [r.menu_item_id for r in rows]


async def _top_seller_items(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    exclude: set[uuid.UUID],
    limit: int,
    hour: int | None,
) -> list[uuid.UUID]:
    """熱銷加購: highest-qty CLOSED items, optionally scoped to a Taipei hour-of-day."""
    stmt = (
        select(OrderLine.menu_item_id, func.sum(OrderLine.qty).label("qty"))
        .join(Order, OrderLine.order_id == Order.id)
        .where(
            Order.tenant_id == tenant_id,
            Order.store_id == store_id,
            Order.status == OrderStatus.CLOSED,
            Order.deleted_at.is_(None),
        )
    )
    if hour is not None:
        stmt = stmt.where(func.extract("hour", func.timezone("Asia/Taipei", Order.closed_at)) == hour)
    if exclude:
        stmt = stmt.where(OrderLine.menu_item_id.notin_(exclude))
    stmt = stmt.group_by(OrderLine.menu_item_id).order_by(func.sum(OrderLine.qty).desc()).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [r.menu_item_id for r in rows]


async def get_recommendations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    cart_item_ids: list[uuid.UUID] | None = None,
    limit: int = 5,
) -> list[RecommendedItem]:
    """規則版智慧加購推薦 (docs/22 #21/#25 先行版, 之後再上 AI 個人化)。

    Two rule tiers, cheapest/most-specific signal first:
    1. cart_pairing — items most often bought in the same CLOSED order as
       something already in the cart (only tried when the cart isn't empty).
    2. top_seller — highest-qty items in the current Asia/Taipei hour-of-day
       bucket (時段情境, #25 先行版); falls back to all-hours if this store
       has no history yet for the exact current hour.
    Both tiers skip items already in the cart/already picked, soft-deleted,
    unavailable, or outside their 時段菜單 window. #25 的人數/天氣情境未收集
    對應資料, 留給未來 AI 版本。
    """
    cart_ids = set(cart_item_ids or [])
    seen = set(cart_ids)
    picks: list[tuple[uuid.UUID, Literal["cart_pairing", "top_seller"]]] = []

    if cart_ids:
        for item_id in await _co_occurring_items(
            session, tenant_id=tenant_id, store_id=store_id, cart_ids=cart_ids, exclude=seen, limit=limit
        ):
            picks.append((item_id, "cart_pairing"))
            seen.add(item_id)

    if len(picks) < limit:
        hour = datetime.now(_TPE).hour
        for item_id in await _top_seller_items(
            session, tenant_id=tenant_id, store_id=store_id, exclude=seen, limit=limit - len(picks), hour=hour
        ):
            picks.append((item_id, "top_seller"))
            seen.add(item_id)

    if len(picks) < limit:
        for item_id in await _top_seller_items(
            session, tenant_id=tenant_id, store_id=store_id, exclude=seen, limit=limit - len(picks), hour=None
        ):
            picks.append((item_id, "top_seller"))
            seen.add(item_id)

    if not picks:
        return []

    ids = [p[0] for p in picks]
    rows = (
        await session.execute(
            select(MenuItem).where(
                MenuItem.id.in_(ids),
                MenuItem.tenant_id == tenant_id,
                MenuItem.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    now = datetime.now(_TPE).time()

    out: list[RecommendedItem] = []
    for item_id, reason in picks:
        item = by_id.get(item_id)
        if item is None or not item.is_available or not is_item_available_now(item, now):
            continue
        out.append(RecommendedItem(menu_item_id=item.id, name=item.name, price=item.price, reason=reason))
    return out


# ──────────────────────────────────────────────────────────────────────────
# 口味選項組/加價購 (modifier groups)
# ──────────────────────────────────────────────────────────────────────────


async def create_modifier_group(
    session: AsyncSession,
    item_id: uuid.UUID,
    payload: ModifierGroupCreate,
    *,
    tenant_id: uuid.UUID,
) -> ModifierGroupResponse:
    item = await _load_item(session, item_id, tenant_id)
    if payload.max_select < payload.min_select:
        raise ConflictError(
            message="max_select 不能小於 min_select",
            details={"min_select": payload.min_select, "max_select": payload.max_select},
        )
    group = ModifierGroup(
        tenant_id=tenant_id,
        menu_item_id=item.id,
        name=payload.name,
        min_select=payload.min_select,
        max_select=payload.max_select,
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
                is_active=opt.is_active,
            )
        )
    await session.flush()
    await session.refresh(group, attribute_names=["options"])
    await audit(
        session,
        action="modifier_group.created",
        tenant_id=tenant_id,
        store_id=item.store_id,
        target=("modifier_groups", group.id),
        after={"menu_item_id": str(item.id), "name": payload.name, "option_count": len(payload.options)},
    )
    return ModifierGroupResponse.model_validate(group)


async def list_modifier_groups(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> list[ModifierGroupResponse]:
    stmt = (
        select(ModifierGroup)
        .where(
            ModifierGroup.menu_item_id == item_id,
            ModifierGroup.tenant_id == tenant_id,
            ModifierGroup.deleted_at.is_(None),
            ModifierGroup.is_active.is_(True),
        )
        .order_by(ModifierGroup.sort_order)
    )
    groups = (await session.execute(stmt)).scalars().all()
    for g in groups:
        await session.refresh(g, attribute_names=["options"])
    return [ModifierGroupResponse.model_validate(g) for g in groups]


async def delete_modifier_group(
    session: AsyncSession,
    group_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    row = (
        await session.execute(
            select(ModifierGroup).where(
                ModifierGroup.id == group_id,
                ModifierGroup.tenant_id == tenant_id,
                ModifierGroup.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"modifier group {group_id} not found",
            details={"group_id": str(group_id)},
        )
    from datetime import UTC, datetime

    row.deleted_at = datetime.now(UTC)
    await session.flush()
    await audit(
        session,
        action="modifier_group.deleted",
        tenant_id=tenant_id,
        store_id=None,
        target=("modifier_groups", row.id),
        before={"name": row.name},
    )


# ──────────────────────────────────────────────────────────────────────────
# 套餐組合 (combo components)
# ──────────────────────────────────────────────────────────────────────────


async def add_combo_component(
    session: AsyncSession,
    combo_item_id: uuid.UUID,
    payload: ComboComponentCreate,
    *,
    tenant_id: uuid.UUID,
) -> ComboComponentResponse:
    combo_item = await _load_item(session, combo_item_id, tenant_id)
    if payload.component_item_id == combo_item_id:
        raise ConflictError(
            message="套餐不能把自己當作組成品項", details={"combo_item_id": str(combo_item_id)}
        )
    await _load_item(session, payload.component_item_id, tenant_id)
    row = ComboComponent(
        tenant_id=tenant_id,
        combo_item_id=combo_item.id,
        component_item_id=payload.component_item_id,
        qty=payload.qty,
        sort_order=payload.sort_order,
    )
    session.add(row)
    await session.flush()
    await audit(
        session,
        action="combo_component.added",
        tenant_id=tenant_id,
        store_id=combo_item.store_id,
        target=("combo_components", row.id),
        after={"combo_item_id": str(combo_item_id), "component_item_id": str(payload.component_item_id)},
    )
    return ComboComponentResponse.model_validate(row)


async def list_combo_components(
    session: AsyncSession,
    combo_item_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> list[ComboComponentResponse]:
    stmt = (
        select(ComboComponent)
        .where(ComboComponent.combo_item_id == combo_item_id, ComboComponent.tenant_id == tenant_id)
        .order_by(ComboComponent.sort_order)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [ComboComponentResponse.model_validate(r) for r in rows]


async def remove_combo_component(
    session: AsyncSession,
    component_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    row = (
        await session.execute(
            select(ComboComponent).where(
                ComboComponent.id == component_id, ComboComponent.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            message=f"combo component {component_id} not found",
            details={"component_id": str(component_id)},
        )
    await session.delete(row)
    await session.flush()
    await audit(
        session,
        action="combo_component.removed",
        tenant_id=tenant_id,
        store_id=None,
        target=("combo_components", component_id),
        before={"combo_item_id": str(row.combo_item_id), "component_item_id": str(row.component_item_id)},
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
    "add_combo_component",
    "create_category",
    "create_item",
    "create_modifier_group",
    "delete_category",
    "delete_item",
    "delete_modifier_group",
    "get_item",
    "get_recommendations",
    "is_item_available_now",
    "list_categories",
    "list_combo_components",
    "list_items",
    "list_modifier_groups",
    "remove_combo_component",
    "set_translation",
    "update_category",
    "update_item",
]
