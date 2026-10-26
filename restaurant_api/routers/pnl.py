"""``/reports/daily-pnl``: the owner's daily real-profit report (admin-gated, read-only).

JSON by default; ``?format=html`` returns a page the owner can open on a phone. The route sits under
``/reports`` next to the POS sales reports but is a separate router, so it does not depend on them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import HTMLResponse

from ..api.auth import Admin
from ..api.deps import DbSession
from ..schemas.daily_pnl import DailyPnlReport
from ..services import daily_pnl_service
from ..services.daily_pnl_html import render_html

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/daily-pnl",
    response_model=DailyPnlReport,
    summary="Daily real profit: what is left after tax, ingredients, people and rent",
)
async def get_daily_pnl(
    session: DbSession,
    response: Response,
    _admin: Admin,
    store_id: Annotated[
        uuid.UUID | None,
        Query(description="Store to report on. Optional when the system has exactly one active store."),
    ] = None,
    business_date: Annotated[
        date | None,
        Query(alias="date", description="Business date, YYYY-MM-DD. Defaults to today (Asia/Taipei)."),
    ] = None,
    fmt: Annotated[
        Literal["json", "html"],
        Query(alias="format", description="'html' returns a human-readable page."),
    ] = "json",
) -> DailyPnlReport | HTMLResponse:
    day = business_date or datetime.now(daily_pnl_service.TAIPEI).date()
    report = await daily_pnl_service.build_daily_pnl(session, store_id=store_id, business_date=day)
    headers = {"Cache-Control": "no-store"}  # financial data: never cached by a browser or proxy
    if fmt == "html":
        return HTMLResponse(render_html(report), headers=headers)
    response.headers.update(headers)
    return report
