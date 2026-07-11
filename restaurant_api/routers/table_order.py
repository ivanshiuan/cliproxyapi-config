"""FastAPI router — ``/t/{qr_token}`` (顧客掃碼點餐, P2).

Public: no tenant header, no login — the unguessable per-table ``qr_token`` is
the credential and scopes everything to that one table's live seating.
"""

from __future__ import annotations

from fastapi import APIRouter, Path

from ..api.deps import DbSession
from ..schemas.table_order import CartSubmit, GuestBill, TableContext
from ..services import table_order_service

_P_TOKEN = Path(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

router = APIRouter(prefix="/t", tags=["table-order"])


@router.get(
    "/{qr_token}/context",
    response_model=TableContext,
    summary="掃碼落地: 桌位資訊 + 可否點餐 + 菜單 (顧客用, 無祕密欄位)",
)
async def get_context(
    session: DbSession,
    qr_token: str = _P_TOKEN,
) -> TableContext:
    return await table_order_service.get_context(session, qr_token)


@router.post(
    "/{qr_token}/cart",
    response_model=GuestBill,
    summary="送出購物車 → 加進本桌帳單 (需已開桌; 價格伺服器快照)",
)
async def submit_cart(
    payload: CartSubmit,
    session: DbSession,
    qr_token: str = _P_TOKEN,
) -> GuestBill:
    return await table_order_service.submit_cart(session, qr_token, payload)


@router.get(
    "/{qr_token}/bill",
    response_model=GuestBill,
    summary="本桌目前帳單 (顧客視角)",
)
async def get_bill(
    session: DbSession,
    qr_token: str = _P_TOKEN,
) -> GuestBill:
    return await table_order_service.get_bill(session, qr_token)


__all__ = ["router"]
