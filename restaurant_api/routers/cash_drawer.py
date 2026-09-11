"""FastAPI router — ``/cash-drawer`` (交班/關帳, P7.2).

Thin layer over ``services/cash_drawer_service``. Guided-closing flow:
GET current → GET expected (target to count against) → POST close (server
computes variance, never trusts a client-supplied expected number).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from ..api.deps import DbSession, TenantId
from ..schemas.cash_drawer import (
    CashDrawerSessionResponse,
    DrawerCloseRequest,
    DrawerCloseResult,
    DrawerExpectedBreakdown,
    DrawerOpenRequest,
)
from ..services import cash_drawer_service

_Q_STORE_ID_REQ = Query()
_Q_REGISTER_NO = Query(default=None)

router = APIRouter(prefix="/cash-drawer", tags=["cash-drawer"])


@router.get(
    "/current",
    response_model=CashDrawerSessionResponse | None,
    summary="目前這台收銀機是否已開班",
)
async def get_current(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    register_no: str | None = _Q_REGISTER_NO,
) -> CashDrawerSessionResponse | None:
    return await cash_drawer_service.get_current(
        session, tenant_id=tenant_id, store_id=store_id, register_no=register_no
    )


@router.post(
    "/open",
    response_model=CashDrawerSessionResponse,
    summary="開班 (輸入備用金)",
)
async def open_drawer(
    payload: DrawerOpenRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CashDrawerSessionResponse:
    return await cash_drawer_service.open_drawer(session, payload, tenant_id=tenant_id)


@router.get(
    "/{drawer_session_id}/expected",
    response_model=DrawerExpectedBreakdown,
    summary="關帳前預覽: 現在應有多少現金 (引導式清點目標)",
)
async def preview_close(
    drawer_session_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> DrawerExpectedBreakdown:
    return await cash_drawer_service.preview_close(
        session, drawer_session_id, tenant_id=tenant_id
    )


@router.post(
    "/{drawer_session_id}/close",
    response_model=DrawerCloseResult,
    summary="交班/關帳 (輸入實際清點金額, 系統算差異 + 日結報告)",
)
async def close_drawer(
    drawer_session_id: uuid.UUID,
    payload: DrawerCloseRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> DrawerCloseResult:
    return await cash_drawer_service.close_drawer(
        session, drawer_session_id, payload, tenant_id=tenant_id
    )


__all__ = ["router"]
