"""FastAPI router — ``/reports`` (營運報表 + 後台匯出).

Read-only, thin over ``services/reports_service``. Everything is scoped to a
store and a ``business_date`` range (a missing ``date_to`` = single day).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, Response

from ..api.deps import DbSession, TenantId
from ..schemas.reports import ChannelReport, HourlyReport, SalesReport, TopItem
from ..services import reports_service

_Q_STORE_ID_REQ = Query()
_Q_DATE_FROM = Query(description="起始營業日 YYYY-MM-DD")
_Q_DATE_TO = Query(default=None, description="結束營業日 (省略=單日)")
_Q_LIMIT = Query(default=20, ge=1, le=100)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/sales", response_model=SalesReport, summary="日結/區間營收彙總")
async def sales(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    date_from: date = _Q_DATE_FROM,
    date_to: date | None = _Q_DATE_TO,
) -> SalesReport:
    return await reports_service.sales_report(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/top-items", response_model=list[TopItem], summary="熱銷品項排行")
async def top_items(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    date_from: date = _Q_DATE_FROM,
    date_to: date | None = _Q_DATE_TO,
    limit: int = _Q_LIMIT,
) -> list[TopItem]:
    return await reports_service.top_items(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.get("/hourly", response_model=HourlyReport, summary="時段分析 — 哪個時段賺錢")
async def hourly(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    date_from: date = _Q_DATE_FROM,
    date_to: date | None = _Q_DATE_TO,
) -> HourlyReport:
    return await reports_service.hourly_report(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/channels", response_model=ChannelReport, summary="通路分佈 — 內用/外帶/外送營收占比")
async def channels(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    date_from: date = _Q_DATE_FROM,
    date_to: date | None = _Q_DATE_TO,
) -> ChannelReport:
    return await reports_service.channel_report(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/export/orders.csv",
    summary="訂單明細 CSV 匯出 (記帳/對帳用)",
    response_class=Response,
)
async def export_orders_csv(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    date_from: date = _Q_DATE_FROM,
    date_to: date | None = _Q_DATE_TO,
) -> Response:
    body = await reports_service.orders_csv(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        date_from=date_from,
        date_to=date_to,
    )
    end = date_to or date_from
    filename = f"orders_{date_from.isoformat()}_{end.isoformat()}.csv"
    # UTF-8 BOM so Excel opens the Chinese columns without mojibake.
    return Response(
        content="﻿" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
