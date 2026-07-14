"""FastAPI router — ``/online-takeout`` (線上外帶, P7.5).

Order-ahead, pay-at-counter: no payment gateway yet (🔴 in docs/22), so the
customer submits from their phone and the order stays OPEN until staff
collects cash when they arrive. Creation is public (no login, matching the
table-QR guest flow) — list/collect are staff-facing POS operations.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
from ..schemas.orders import OrderResponse
from ..schemas.pos_orders import (
    CheckoutCashRequest,
    CheckoutResult,
    OnlineTakeoutRequest,
    OnlineTakeoutResult,
)
from ..services import pos_order_service

_Q_STORE_ID_REQ = Query()

router = APIRouter(prefix="/online-takeout", tags=["online-takeout"])


@router.post(
    "",
    response_model=OnlineTakeoutResult,
    status_code=status.HTTP_201_CREATED,
    summary="線上外帶 — 顧客手機下單預約自取 (不綁桌, 到店現場收款)",
)
async def create_online_takeout(
    payload: OnlineTakeoutRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OnlineTakeoutResult:
    return await pos_order_service.create_online_takeout(session, payload, tenant_id=tenant_id)


@router.get(
    "/pending",
    response_model=list[OrderResponse],
    summary="外帶待取面板 — 尚未取件的線上外帶單, 依下單時間排序",
)
async def list_pending(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
) -> list[OrderResponse]:
    return await pos_order_service.list_pending_online_takeout(
        session, tenant_id=tenant_id, store_id=store_id
    )


@router.post(
    "/{order_id}/collect",
    response_model=CheckoutResult,
    summary="顧客到店取件 — 現金收款並關單",
)
async def collect(
    order_id: uuid.UUID,
    payload: CheckoutCashRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CheckoutResult:
    return await pos_order_service.collect_online_takeout(
        session, order_id, payload, tenant_id=tenant_id
    )


__all__ = ["router"]
