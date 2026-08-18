"""FastAPI router — ``/tables`` (桌位管理, 好點 POS 對照 G3).

Thin HTTP layer over ``services/tables_service``: parse, inject, respond.
Create enforces per-store live-name uniqueness (409), delete is a soft
delete so historical orders keep their table reference and the name frees
up for the next floor re-layout.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from ..api.deps import DbSession, TenantId
from ..api.errors import DomainError, ErrorBody, domain_error_handler
from ..schemas.tables import TableCreateRequest, TablePatchRequest, TableResponse
from ..services import tables_service

router = APIRouter(prefix="/tables", tags=["tables"])

# Module-level Query() singletons (ruff B008 avoidance, FastAPI metadata kept).
_Q_STORE_ID = Query(default=None)
_Q_INCLUDE_INACTIVE = Query(default=False)
_Q_LIMIT = Query(default=500, ge=1, le=1000)


@router.post(
    "",
    response_model=TableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立桌位 (同店活桌名重複 → 409)",
)
async def create_table_endpoint(
    payload: TableCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> TableResponse:
    return await tables_service.create_table(session, payload, tenant_id=tenant_id)


@router.get(
    "",
    response_model=list[TableResponse],
    summary="列出桌位 (預設只回 is_active 且未軟刪, 依 sort_order,name 排序)",
)
async def list_tables_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    include_inactive: bool = _Q_INCLUDE_INACTIVE,
    limit: int = _Q_LIMIT,
) -> list[TableResponse]:
    return await tables_service.list_tables(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        include_inactive=include_inactive,
        limit=limit,
    )


@router.patch(
    "/{table_id}",
    response_model=TableResponse,
    summary="更新桌位 (name/zone/capacity/sort_order/is_active)",
)
async def patch_table_endpoint(
    table_id: uuid.UUID,
    payload: TablePatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> TableResponse:
    return await tables_service.patch_table(
        session, table_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/{table_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="軟刪桌位 (deleted_at; 同名可重建)",
)
async def delete_table_endpoint(
    table_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await tables_service.delete_table(session, table_id, tenant_id=tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# Self-mount on the shared FastAPI app + register the error envelope handler.
# Same pattern as routers/orders.py — mounting is guarded so importing this
# module is idempotent whether main.py wires it in or a test imports it first.
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
        if getattr(existing_route, "path", "").startswith("/tables"):
            return
    app.include_router(router)
    _ensure_envelope_handler()


# NOTE: ``_mount()`` is no longer called at import time — main.py mounts
# the router explicitly. Tests can call _mount() if they need to bootstrap
# without going through main.

__all__ = ["_mount", "router"]
