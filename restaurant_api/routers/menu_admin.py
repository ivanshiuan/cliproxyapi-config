"""FastAPI router — ``/menu`` (菜單後台管理).

Backoffice CRUD for the menu master: categories, sellable items (with the
停售/恢復 quick toggle), reusable modifier groups (甜度/冰量/加料) with
nested option creation, and the item ↔ modifier-group link table.

All business logic lives in ``services/menu_admin_service``; this module
only parses requests, injects dependencies, and shapes responses.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from ..api.deps import DbSession, TenantId
from ..api.errors import DomainError, ErrorBody, domain_error_handler
from ..schemas.menu_admin import (
    CategoryCreateRequest,
    CategoryPatchRequest,
    CategoryResponse,
    ItemAvailabilityRequest,
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
from ..services import menu_admin_service

# Module-level Query() singletons — ruff B008 avoidance.
_Q_STORE_ID = Query(default=None)
_Q_CATEGORY_ID = Query(default=None)
_Q_INCLUDE_UNAVAILABLE = Query(default=True)

router = APIRouter(prefix="/menu", tags=["menu"])


# ──────────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立菜單分類",
)
async def create_category_endpoint(
    payload: CategoryCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CategoryResponse:
    return await menu_admin_service.create_category(session, payload, tenant_id=tenant_id)


@router.get(
    "/categories",
    response_model=list[CategoryResponse],
    summary="列出菜單分類(依 sort_order)",
)
async def list_categories_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
) -> list[CategoryResponse]:
    return await menu_admin_service.list_categories(
        session, tenant_id=tenant_id, store_id=store_id
    )


@router.patch(
    "/categories/{category_id}",
    response_model=CategoryResponse,
    summary="更新分類 (name / sort_order / is_active)",
)
async def patch_category_endpoint(
    category_id: uuid.UUID,
    payload: CategoryPatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CategoryResponse:
    return await menu_admin_service.patch_category(
        session, category_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="軟刪除分類(deleted_at)",
)
async def delete_category_endpoint(
    category_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_admin_service.delete_category(session, category_id, tenant_id=tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/items",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立菜單品項(SKU 同租戶唯一, 重複回 409)",
)
async def create_item_endpoint(
    payload: ItemCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ItemResponse:
    return await menu_admin_service.create_item(session, payload, tenant_id=tenant_id)


@router.get(
    "/items",
    response_model=list[ItemResponse],
    summary="列出品項(可依 category_id 過濾、預設含停售)",
)
async def list_items_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    category_id: uuid.UUID | None = _Q_CATEGORY_ID,
    include_unavailable: bool = _Q_INCLUDE_UNAVAILABLE,
) -> list[ItemResponse]:
    return await menu_admin_service.list_items(
        session,
        tenant_id=tenant_id,
        category_id=category_id,
        include_unavailable=include_unavailable,
    )


@router.patch(
    "/items/{item_id}",
    response_model=ItemResponse,
    summary="更新品項 (name / description / price / category_id / is_available / allergens)",
)
async def patch_item_endpoint(
    item_id: uuid.UUID,
    payload: ItemPatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ItemResponse:
    return await menu_admin_service.patch_item(
        session, item_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="軟刪除品項(deleted_at; SKU 可重用)",
)
async def delete_item_endpoint(
    item_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_admin_service.delete_item(session, item_id, tenant_id=tenant_id)


@router.post(
    "/items/{item_id}/availability",
    response_model=ItemResponse,
    summary="停售 / 恢復販售快捷開關",
)
async def set_item_availability_endpoint(
    item_id: uuid.UUID,
    payload: ItemAvailabilityRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ItemResponse:
    return await menu_admin_service.set_item_availability(
        session, item_id, tenant_id=tenant_id, is_available=payload.is_available
    )


# ──────────────────────────────────────────────────────────────────────────
# Modifier groups + options
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/modifier-groups",
    response_model=ModifierGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立加料/客製群組(可 nested 一次帶入 options)",
)
async def create_modifier_group_endpoint(
    payload: ModifierGroupCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ModifierGroupResponse:
    return await menu_admin_service.create_modifier_group(
        session, payload, tenant_id=tenant_id
    )


@router.get(
    "/modifier-groups",
    response_model=list[ModifierGroupResponse],
    summary="列出客製群組(含 options)",
)
async def list_modifier_groups_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
) -> list[ModifierGroupResponse]:
    return await menu_admin_service.list_modifier_groups(
        session, tenant_id=tenant_id, store_id=store_id
    )


@router.patch(
    "/modifier-groups/{group_id}",
    response_model=ModifierGroupResponse,
    summary="更新客製群組(min/max 邊界重新驗證)",
)
async def patch_modifier_group_endpoint(
    group_id: uuid.UUID,
    payload: ModifierGroupPatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ModifierGroupResponse:
    return await menu_admin_service.patch_modifier_group(
        session, group_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/modifier-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="軟刪除客製群組",
)
async def delete_modifier_group_endpoint(
    group_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_admin_service.delete_modifier_group(session, group_id, tenant_id=tenant_id)


@router.post(
    "/modifier-groups/{group_id}/options",
    response_model=ModifierOptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="在群組下新增選項",
)
async def create_modifier_option_endpoint(
    group_id: uuid.UUID,
    payload: ModifierOptionCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ModifierOptionResponse:
    return await menu_admin_service.create_modifier_option(
        session, group_id, payload, tenant_id=tenant_id
    )


@router.patch(
    "/modifier-options/{option_id}",
    response_model=ModifierOptionResponse,
    summary="更新選項 (name / price_delta / is_available / sort_order)",
)
async def patch_modifier_option_endpoint(
    option_id: uuid.UUID,
    payload: ModifierOptionPatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ModifierOptionResponse:
    return await menu_admin_service.patch_modifier_option(
        session, option_id, payload, tenant_id=tenant_id
    )


# ──────────────────────────────────────────────────────────────────────────
# Item ↔ modifier-group links
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/items/{item_id}/modifier-groups",
    response_model=ItemModifierLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="把客製群組掛到品項(重複掛回 409)",
)
async def link_modifier_group_endpoint(
    item_id: uuid.UUID,
    payload: ItemModifierLinkRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ItemModifierLinkResponse:
    return await menu_admin_service.link_modifier_group(
        session, item_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/items/{item_id}/modifier-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="解除品項與客製群組的連結",
)
async def unlink_modifier_group_endpoint(
    item_id: uuid.UUID,
    group_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await menu_admin_service.unlink_modifier_group(
        session, item_id, group_id, tenant_id=tenant_id
    )


# ──────────────────────────────────────────────────────────────────────────
# Self-mount on the shared FastAPI app + register the error envelope handler.
# Same pattern as routers/orders.py — main.py owns "real" wiring; tests can
# call ``_mount()`` to bootstrap without going through main.
# ──────────────────────────────────────────────────────────────────────────


def _ensure_envelope_handler() -> None:
    """Register the DomainError handler on the shared app exactly once."""
    from ..main import app

    already = any(
        getattr(handler, "__wrapped_for_domain_error__", False)
        for handler in (
            (app.exception_handlers.get(DomainError) and [app.exception_handlers[DomainError]]) or []
        )
    )
    if already:
        return

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, DomainError):
            return await domain_error_handler(request, exc)
        body = ErrorBody(code="INTERNAL", message=str(exc)).model_dump()
        return JSONResponse(status_code=500, content={"error": body})

    _handler.__wrapped_for_domain_error__ = True  # type: ignore[attr-defined]
    app.add_exception_handler(DomainError, _handler)


def _mount() -> None:
    """Attach this router to the shared FastAPI app, idempotently."""
    from ..main import app

    for existing_route in app.router.routes:
        if getattr(existing_route, "path", "").startswith("/menu"):
            return
    app.include_router(router)
    _ensure_envelope_handler()


__all__ = ["_mount", "router"]
