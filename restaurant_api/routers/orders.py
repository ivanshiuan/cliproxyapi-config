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

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from ..api.deps import DbSession, Messenger, TenantId
from ..api.errors import DomainError, ErrorBody, domain_error_handler
from ..models.orders import OrderSource, OrderStatus, ServiceType
from ..schemas.orders import (
    OrderCloseRequest,
    OrderCreateRequest,
    OrderLinesAddRequest,
    OrderListResponse,
    OrderRefundRequest,
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
    "",
    response_model=OrderListResponse,
    summary="List order summaries with status/date/store/type/source filters.",
)
async def list_orders_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    status_filter: Annotated[
        Literal["open", "closed", "voided", "refunded"] | None,
        Query(alias="status", description="未結單即 status=open"),
    ] = None,
    business_date: Annotated[dt.date | None, Query()] = None,
    store_id: Annotated[uuid.UUID | None, Query()] = None,
    service_type: Annotated[
        Literal["dine_in", "takeout", "delivery"] | None, Query()
    ] = None,
    order_source: Annotated[
        Literal["pos", "qr", "line", "ubereats", "foodpanda", "phone", "other"] | None,
        Query(),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderListResponse:
    """``GET /orders`` — paged summaries (id, order_no, status, 取餐方式,
    來源, 桌號, opened_at, 總額), newest first."""
    return await orders_service.list_orders(
        session=session,
        tenant_id=tenant_id,
        status=OrderStatus(status_filter) if status_filter else None,
        business_date=business_date,
        store_id=store_id,
        service_type=ServiceType(service_type) if service_type else None,
        order_source=OrderSource(order_source) if order_source else None,
        limit=limit,
        offset=offset,
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
    messenger: Messenger,
) -> OrderResponse:
    return await orders_service.close_order(
        session=session,
        order_id=order_id,
        tenant_id=tenant_id,
        closed_at=payload.closed_at,
        messenger=messenger,
    )


@router.post(
    "/{order_id}/lines",
    response_model=OrderResponse,
    summary="加點 — append lines to an OPEN order (409 otherwise).",
)
async def add_lines_endpoint(
    order_id: uuid.UUID,
    payload: OrderLinesAddRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await orders_service.add_lines(
        session=session,
        order_id=order_id,
        tenant_id=tenant_id,
        lines=list(payload.lines),
    )


@router.post(
    "/{order_id}/refund",
    response_model=OrderResponse,
    summary="Refund a CLOSED order — stamps refunded_at and writes an audit row.",
)
async def refund_order_endpoint(
    order_id: uuid.UUID,
    payload: OrderRefundRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await orders_service.refund_order(
        session=session,
        order_id=order_id,
        tenant_id=tenant_id,
        reason=payload.reason,
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
