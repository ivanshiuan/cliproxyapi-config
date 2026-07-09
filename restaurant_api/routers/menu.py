"""FastAPI router — ``/menu`` (catalog CRUD for categories + items).

Thin parse-validate-respond layer over ``services/menu_service``. The POS,
tablet kiosk, and KDS all read the menu through these endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
from ..schemas.menu import (
    MenuCategoryCreate,
    MenuCategoryResponse,
    MenuCategoryUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemUpdate,
)
from ..services import menu_service

_Q_STORE_ID = Query(default=None)
_Q_CATEGORY_ID = Query(default=None)
_Q_INCLUDE_INACTIVE = Query(default=False)
_Q_AVAILABLE_ONLY = Query(default=False)
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
    limit: int = _Q_LIMIT,
) -> list[MenuItemResponse]:
    return await menu_service.list_items(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        category_id=category_id,
        available_only=available_only,
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
) -> MenuItemResponse:
    return await menu_service.get_item(session, item_id, tenant_id=tenant_id)


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


__all__ = ["router"]
