"""FastAPI router for ``/orders`` — order lifecycle endpoints.

This module wires HTTP → service. All business logic, transactions, and
ledger writes live in ``restaurant_api.services.orders_service``; the
router's job is request parsing, dependency injection, and shaping the
response.

Mounting: this module imports ``restaurant_api.main.app`` and calls
``app.include_router(router)`` at import time. The orchestrator wires
this in via ``from restaurant_api.routers import orders  # noqa: F401``
at the bottom of main.py (or via any side-effect import). Tests import
the app and the router is therefore active without further wiring.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from ..api.deps import DbSession, Messenger, TenantId
from ..api.errors import DomainError, ErrorBody, domain_error_handler
from ..schemas.orders import (
    OrderCloseRequest,
    OrderCreateRequest,
    OrderResponse,
    OrderVoidRequest,
)
from ..services import orders_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    response_model=None,  # we hand-build the response to control the status code
    status_code=status.HTTP_201_CREATED,
    summary="Create a new order (or replay idempotently by external_pos_id).",
)
async def create_order_endpoint(
    payload: OrderCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
    messenger: Messenger,
) -> JSONResponse:
    """``POST /orders`` — create or idempotently replay.

    When ``external_pos_id`` matches an existing order for this
    (tenant, store) tuple, we return that order with HTTP 200 instead of
    creating a duplicate. Brand-new orders return 201.
    """
    response, created = await orders_service.create_order(
        session=session, payload=payload, tenant_id=tenant_id, messenger=messenger,
    )
    code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(
        status_code=code,
        content=response.model_dump(mode="json"),
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Fetch one order with its lines, discounts, and payments.",
)
async def get_order_endpoint(
    order_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await orders_service.get_order(
        session=session, order_id=order_id, tenant_id=tenant_id,
    )


@router.post(
    "/{order_id}/close",
    response_model=OrderResponse,
    summary="Close an open order — sets status=closed and stamps closed_at.",
)
async def close_order_endpoint(
    order_id: uuid.UUID,
    payload: OrderCloseRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await orders_service.close_order(
        session=session,
        order_id=order_id,
        tenant_id=tenant_id,
        closed_at=payload.closed_at,
    )


@router.post(
    "/{order_id}/void",
    response_model=OrderResponse,
    summary="Void an order — writes reversing stock_movements; never edits the ledger.",
)
async def void_order_endpoint(
    order_id: uuid.UUID,
    payload: OrderVoidRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await orders_service.void_order(
        session=session,
        order_id=order_id,
        tenant_id=tenant_id,
        reason=payload.reason,
    )


# ──────────────────────────────────────────────────────────────────────────
# Self-mount on the shared FastAPI app + register the error envelope handler.
# Doing this at import time keeps main.py free of per-router wiring while
# still giving us the consistent ``{"error": {...}}`` envelope on 4xx/5xx.
# ──────────────────────────────────────────────────────────────────────────


def _ensure_envelope_handler() -> None:
    """Register the DomainError handler on the shared app exactly once.

    Without this, FastAPI's default HTTPException handler would serialise
    DomainError as ``{"detail": {...}}`` — we want ``{"error": {...}}``.
    """
    from ..main import app

    # Idempotent: only register if not already present.
    already = any(
        getattr(handler, "__wrapped_for_domain_error__", False)
        for handler in ((app.exception_handlers.get(DomainError) and [app.exception_handlers[DomainError]]) or [])
    )
    if already:
        return

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, DomainError):
            return await domain_error_handler(request, exc)
        # Fallback (shouldn't happen — only registered for DomainError):
        body = ErrorBody(code="INTERNAL", message=str(exc)).model_dump()
        return JSONResponse(status_code=500, content={"error": body})

    _handler.__wrapped_for_domain_error__ = True  # type: ignore[attr-defined]
    app.add_exception_handler(DomainError, _handler)


def _mount() -> None:
    """Attach this router to the shared FastAPI app, idempotently."""
    from ..main import app

    for existing_route in app.router.routes:
        # Routes carry ``path`` attrs; we look for our prefix to avoid
        # double-mounting if this module is imported multiple times.
        if getattr(existing_route, "path", "").startswith("/orders"):
            return
    app.include_router(router)
    _ensure_envelope_handler()


# NOTE: ``_mount()`` is no longer called at import time — main.py mounts
# the router explicitly. Tests can call _mount() if they need to bootstrap
# without going through main.

__all__ = ["_mount", "router"]
