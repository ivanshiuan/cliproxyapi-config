"""FastAPI router — ``/menu`` (catalog CRUD for categories + items).

Thin parse-validate-respond layer over ``services/menu_service``. The POS,
tablet kiosk, and KDS all read the menu through these endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
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
)
from ..services import menu_service

_Q_STORE_ID = Query(default=None)
_Q_CATEGORY_ID = Query(default=None)
_Q_INCLUDE_INACTIVE = Query(default=False)
_Q_AVAILABLE_ONLY = Query(default=False)
_Q_AVAILABLE_NOW = Query(default=False, description="時段菜單: 只列現在時段供應的品項")
_Q_LANG = Query(default=None, description="多語系菜單: 回傳這個語言的翻譯 (缺翻譯時退回中文)")
_Q_LIMIT = Query(default=500, ge=1, le=1000)

router = APIRouter(prefix="/menu", tags=["menu"])


# ── Categories ──────────────────────────────────────────────────────────────


@router.post(
    "/categories",
    response_model=MenuCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立菜單分類",
)
async def create_category(
    payload: MenuCategoryCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> MenuCategoryResponse:
    return await menu_service.create_category(session, payload, tenant_id=tenant_id)


@router.get(
    "/categories",
    response_model=list[MenuCategoryResponse],
    summary="列出菜單分類",
)
async def list_categories(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    include_inactive: bool = _Q_INCLUDE_INACTIVE,
) -> list[MenuCategoryResponse]:
    return await menu_service.list_categories(
        session, tenant_id=tenant_id, store_id=store_id, include_inactive=include_inactive
    )


@router.patch(
    "/categories/{category_id}",
    response_model=MenuCategoryResponse,
    summary="更新菜單分類",
)
async def update_category(
    category_id: uuid.UUID,
    payload: MenuCategoryUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> MenuCategoryResponse:
    return await menu_service.update_category(
        session, category_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除菜單分類 (軟刪)",
)
async def delete_category(
    category_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_service.delete_category(session, category_id, tenant_id=tenant_id)


# ── Items ───────────────────────────────────────────────────────────────────


@router.post(
    "/items",
    response_model=MenuItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立菜單品項",
)
async def create_item(
    payload: MenuItemCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> MenuItemResponse:
    return await menu_service.create_item(session, payload, tenant_id=tenant_id)


@router.get(
    "/items",
    response_model=list[MenuItemResponse],
    summary="列出菜單品項",
)
async def list_items(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    category_id: uuid.UUID | None = _Q_CATEGORY_ID,
    available_only: bool = _Q_AVAILABLE_ONLY,
    available_now: bool = _Q_AVAILABLE_NOW,
    lang: str | None = _Q_LANG,
    limit: int = _Q_LIMIT,
) -> list[MenuItemResponse]:
    return await menu_service.list_items(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        category_id=category_id,
        available_only=available_only,
        available_now=available_now,
        lang=lang,
        limit=limit,
    )


@router.get(
    "/items/{item_id}",
    response_model=MenuItemResponse,
    summary="查詢單筆品項",
)
async def get_item(
    item_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    lang: str | None = _Q_LANG,
) -> MenuItemResponse:
    return await menu_service.get_item(session, item_id, tenant_id=tenant_id, lang=lang)


@router.patch(
    "/items/{item_id}",
    response_model=MenuItemResponse,
    summary="更新菜單品項",
)
async def update_item(
    item_id: uuid.UUID,
    payload: MenuItemUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> MenuItemResponse:
    return await menu_service.update_item(session, item_id, payload, tenant_id=tenant_id)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除菜單品項 (軟刪)",
)
async def delete_item(
    item_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_service.delete_item(session, item_id, tenant_id=tenant_id)


@router.put(
    "/items/{item_id}/translations",
    response_model=MenuItemResponse,
    summary="多語系菜單 — 設定/覆寫單一語言的翻譯",
)
async def set_translation(
    item_id: uuid.UUID,
    payload: MenuItemTranslationUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> MenuItemResponse:
    return await menu_service.set_translation(session, item_id, payload, tenant_id=tenant_id)


# ── 口味選項組/加價購 (modifier groups) ────────────────────────────────────


@router.post(
    "/items/{item_id}/modifier-groups",
    response_model=ModifierGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立品項的口味選項組/加價購",
)
async def create_modifier_group(
    item_id: uuid.UUID,
    payload: ModifierGroupCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> ModifierGroupResponse:
    return await menu_service.create_modifier_group(session, item_id, payload, tenant_id=tenant_id)


@router.get(
    "/items/{item_id}/modifier-groups",
    response_model=list[ModifierGroupResponse],
    summary="列出品項的口味選項組",
)
async def list_modifier_groups(
    item_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> list[ModifierGroupResponse]:
    return await menu_service.list_modifier_groups(session, item_id, tenant_id=tenant_id)


@router.delete(
    "/modifier-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除口味選項組 (軟刪)",
)
async def delete_modifier_group(
    group_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_service.delete_modifier_group(session, group_id, tenant_id=tenant_id)


# ── 套餐組合 (combo components) ─────────────────────────────────────────────


@router.post(
    "/items/{item_id}/combo-components",
    response_model=ComboComponentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="把品項加進套餐的組成清單",
)
async def add_combo_component(
    item_id: uuid.UUID,
    payload: ComboComponentCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> ComboComponentResponse:
    return await menu_service.add_combo_component(session, item_id, payload, tenant_id=tenant_id)


@router.get(
    "/items/{item_id}/combo-components",
    response_model=list[ComboComponentResponse],
    summary="列出套餐的組成品項",
)
async def list_combo_components(
    item_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> list[ComboComponentResponse]:
    return await menu_service.list_combo_components(session, item_id, tenant_id=tenant_id)


@router.delete(
    "/combo-components/{component_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="從套餐移除組成品項",
)
async def remove_combo_component(
    component_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_service.remove_combo_component(session, component_id, tenant_id=tenant_id)


__all__ = ["router"]
