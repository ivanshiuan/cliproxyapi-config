"""FastAPI router for ``/reports`` — 營運報表 (cloud reporting, G8).

Read-only aggregation endpoints competing with 好點 POS cloud reports
(``docs/13_haodian_pos_comparison.md`` G8) — every report additionally
carries COGS + gross margin. All business logic lives in
``restaurant_api.services.reports_service``; this module does request
parsing, DI, and response shaping only.

All endpoints are admin-gated (back-office owner surface, same posture
as ``/campaigns``).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..api.auth import require_admin
from ..api.deps import DbSession, TenantId
from ..api.errors import DomainError, ErrorBody, domain_error_handler
from ..schemas.reports import (
    DailyReportResponse,
    MonthlyReportResponse,
    PeriodReportResponse,
    ProductsReportResponse,
    ReconciliationReportResponse,
)
from ..services import reports_service

_ADMIN = [Depends(require_admin)]

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/daily",
    response_model=DailyReportResponse,
    summary="單日營運摘要 — 營收、退款、支付別、成本與毛利。",
    dependencies=_ADMIN,
)
async def daily_report_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: Annotated[uuid.UUID, Query(description="Store to report on")],
    business_date: Annotated[date, Query(description="Operational day (business_date)")],
) -> DailyReportResponse:
    return await reports_service.daily_report(
        session, tenant_id=tenant_id, store_id=store_id, business_date=business_date,
    )


@router.get(
    "/monthly",
    response_model=MonthlyReportResponse,
    summary="月報 — 逐日營收/成本/毛利 + 月合計。",
    dependencies=_ADMIN,
)
async def monthly_report_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: Annotated[uuid.UUID, Query(description="Store to report on")],
    year: Annotated[int, Query(description="Calendar year, e.g. 2026")],
    month: Annotated[int, Query(description="Month 1-12")],
) -> MonthlyReportResponse:
    return await reports_service.monthly_report(
        session, tenant_id=tenant_id, store_id=store_id, year=year, month=month,
    )


@router.get(
    "/products",
    response_model=ProductsReportResponse,
    summary="商品銷售排行 — 含每品項成本與毛利, 依營收排序。",
    dependencies=_ADMIN,
)
async def products_report_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: Annotated[uuid.UUID, Query(description="Store to report on")],
    date_from: Annotated[date, Query(description="Range start (business_date, inclusive)")],
    date_to: Annotated[date, Query(description="Range end (business_date, inclusive)")],
    limit: Annotated[int, Query(ge=1, le=200, description="Max rows returned")] = 20,
) -> ProductsReportResponse:
    return await reports_service.products_report(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.get(
    "/period",
    response_model=PeriodReportResponse,
    summary="時段營收 — 以 Asia/Taipei 時鐘過濾 (例: 午餐 11-14)。",
    dependencies=_ADMIN,
)
async def period_report_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: Annotated[uuid.UUID, Query(description="Store to report on")],
    date_from: Annotated[date, Query(description="Range start (business_date, inclusive)")],
    date_to: Annotated[date, Query(description="Range end (business_date, inclusive)")],
    hour_from: Annotated[int, Query(description="Local hour, inclusive (0-23)")],
    hour_to: Annotated[int, Query(description="Local hour, exclusive (1-24)")],
) -> PeriodReportResponse:
    return await reports_service.period_report(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
        hour_from=hour_from,
        hour_to=hour_to,
    )


@router.get(
    "/reconciliation",
    response_model=ReconciliationReportResponse,
    summary="日結對帳 — 未結訂單、退款清單、各支付合計與應有現金。",
    dependencies=_ADMIN,
)
async def reconciliation_report_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    store_id: Annotated[uuid.UUID, Query(description="Store to report on")],
    business_date: Annotated[date, Query(description="Operational day (business_date)")],
) -> ReconciliationReportResponse:
    return await reports_service.reconciliation_report(
        session, tenant_id=tenant_id, store_id=store_id, business_date=business_date,
    )


# ──────────────────────────────────────────────────────────────────────────
# Self-mount on the shared FastAPI app + register the error envelope handler.
# Same pattern as routers/orders.py — main.py mounts explicitly in prod;
# tests call ``_mount()`` (or import-time side effect) to bootstrap.
# ──────────────────────────────────────────────────────────────────────────


def _ensure_envelope_handler() -> None:
    """Register the DomainError handler on the shared app exactly once."""
    from ..main import app

    already = any(
        getattr(handler, "__wrapped_for_domain_error__", False)
        for handler in ((app.exception_handlers.get(DomainError) and [app.exception_handlers[DomainError]]) or [])
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
        if getattr(existing_route, "path", "").startswith("/reports"):
            return
    app.include_router(router)
    _ensure_envelope_handler()


__all__ = ["_mount", "router"]
