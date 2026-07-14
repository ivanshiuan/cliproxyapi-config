"""FastAPI router — ``/tables`` (POS floor plan + table sessions, P1.3).

Thin layer over ``services/table_service``. Two resource families under one
prefix: physical tables (CRUD) and their seatings (open/close/transfer +
the floor-plan board).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..api.deps import DbSession, TenantId
from ..api.pos_auth import PosActor
from ..config import get_settings
from ..database import get_sessionmaker
from ..schemas.orders import OrderResponse
from ..schemas.pos_orders import (
    CheckoutCashRequest,
    CheckoutQuote,
    CheckoutResult,
    DiscountApplyRequest,
    LineAddRequest,
    LineUpdateRequest,
    LineVoidRequest,
    PartialPayRequest,
    PartialPayResult,
    ServiceChargeRequest,
    TakeoutSaleRequest,
)
from ..schemas.tables import (
    DiningTableCreate,
    DiningTableResponse,
    DiningTableUpdate,
    FloorTableView,
    OrderEventResponse,
    TableSessionMerge,
    TableSessionOpen,
    TableSessionResponse,
    TableSessionTransfer,
)
from ..services import event_hub, pos_order_service, table_service

logger = logging.getLogger("restaurant_api")

_Q_STORE_ID = Query(default=None)
_Q_STORE_ID_REQ = Query()
_Q_ACTOR_ID = Query(default=None)
_Q_AFTER = Query(default=0, ge=0)

# WS heartbeat — send a ping frame if no real event flows for this long, so
# idle sockets stay alive through proxies/load-balancers and a dead peer is
# detected on the next send.
_WS_HEARTBEAT_SECONDS = 25.0

router = APIRouter(prefix="/tables", tags=["tables"])


# ── Floor-plan board ─────────────────────────────────────────────────────────


@router.get(
    "/floor",
    response_model=list[FloorTableView],
    summary="樓面桌況板 (每桌 + 現行開桌)",
)
async def get_floor_plan(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
) -> list[FloorTableView]:
    return await table_service.floor_plan(session, tenant_id=tenant_id, store_id=store_id)


# ── Table CRUD ───────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=DiningTableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立桌位",
)
async def create_table(
    payload: DiningTableCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> DiningTableResponse:
    return await table_service.create_table(session, payload, tenant_id=tenant_id)


@router.get(
    "",
    response_model=list[DiningTableResponse],
    summary="列出桌位",
)
async def list_tables(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
) -> list[DiningTableResponse]:
    return await table_service.list_tables(session, tenant_id=tenant_id, store_id=store_id)


@router.patch(
    "/{table_id}",
    response_model=DiningTableResponse,
    summary="更新桌位",
)
async def update_table(
    table_id: uuid.UUID,
    payload: DiningTableUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> DiningTableResponse:
    return await table_service.update_table(session, table_id, payload, tenant_id=tenant_id)


@router.delete(
    "/{table_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除桌位 (軟刪; 有開桌則 409)",
)
async def delete_table(
    table_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await table_service.delete_table(session, table_id, tenant_id=tenant_id)


# ── Table sessions ───────────────────────────────────────────────────────────


@router.post(
    "/{table_id}/open",
    response_model=TableSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="開桌 (入座)",
)
async def open_session(
    table_id: uuid.UUID,
    payload: TableSessionOpen,
    session: DbSession,
    tenant_id: TenantId,
) -> TableSessionResponse:
    return await table_service.open_session(session, table_id, payload, tenant_id=tenant_id)


@router.post(
    "/sessions/{session_id}/close",
    response_model=TableSessionResponse,
    summary="結束開桌 (結帳離桌)",
)
async def close_session(
    session_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    actor_id: uuid.UUID | None = _Q_ACTOR_ID,
) -> TableSessionResponse:
    return await table_service.close_session(
        session, session_id, tenant_id=tenant_id, actor_id=actor_id
    )


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=TableSessionResponse,
    summary="取消開桌 (開錯桌/客人離開)",
)
async def cancel_session(
    session_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    actor_id: uuid.UUID | None = _Q_ACTOR_ID,
) -> TableSessionResponse:
    return await table_service.cancel_session(
        session, session_id, tenant_id=tenant_id, actor_id=actor_id
    )


@router.post(
    "/sessions/{session_id}/merge",
    response_model=OrderResponse,
    summary="併桌 — 把另一桌的帳單併入這一桌 (來源桌釋出)",
)
async def merge_sessions(
    session_id: uuid.UUID,
    payload: TableSessionMerge,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await pos_order_service.merge_sessions(
        session, session_id, payload, tenant_id=tenant_id
    )


@router.post(
    "/sessions/{session_id}/transfer",
    response_model=TableSessionResponse,
    summary="轉桌 (移到另一空桌)",
)
async def transfer_session(
    session_id: uuid.UUID,
    payload: TableSessionTransfer,
    session: DbSession,
    tenant_id: TenantId,
) -> TableSessionResponse:
    return await table_service.transfer_session(
        session, session_id, payload, tenant_id=tenant_id
    )


# ── Session ordering + cash checkout (P1.3b / P1.4) ──────────────────────────


@router.get(
    "/sessions/{session_id}/order",
    response_model=OrderResponse,
    summary="取得 (或開立) 此桌的現行訂單",
)
async def get_session_order(
    session_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await pos_order_service.get_session_order(
        session, session_id, tenant_id=tenant_id
    )


@router.post(
    "/sessions/{session_id}/lines",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="加點一項 (價格由菜單快照)",
)
async def add_line(
    session_id: uuid.UUID,
    payload: LineAddRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await pos_order_service.add_line(
        session, session_id, payload, tenant_id=tenant_id
    )


@router.patch(
    "/order-lines/{line_id}",
    response_model=OrderResponse,
    summary="改量",
)
async def update_line(
    line_id: uuid.UUID,
    payload: LineUpdateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> OrderResponse:
    return await pos_order_service.update_line_qty(
        session, line_id, payload, tenant_id=tenant_id
    )


@router.post(
    "/order-lines/{line_id}/void",
    response_model=OrderResponse,
    summary="退菜 (需權限或主管覆核 + 回沖庫存 + 稽核)",
)
async def void_line(
    line_id: uuid.UUID,
    payload: LineVoidRequest,
    session: DbSession,
    tenant_id: TenantId,
    principal: PosActor,
) -> OrderResponse:
    return await pos_order_service.void_line(
        session, line_id, payload, tenant_id=tenant_id, principal=principal
    )


@router.post(
    "/sessions/{session_id}/discount",
    response_model=OrderResponse,
    summary="套用折扣 (需權限或主管覆核)",
)
async def apply_discount(
    session_id: uuid.UUID,
    payload: DiscountApplyRequest,
    session: DbSession,
    tenant_id: TenantId,
    principal: PosActor,
) -> OrderResponse:
    return await pos_order_service.apply_discount(
        session, session_id, payload, tenant_id=tenant_id, principal=principal
    )


@router.post(
    "/takeout",
    response_model=CheckoutResult,
    status_code=status.HTTP_201_CREATED,
    summary="外帶快速單 (點餐+現金結帳一步完成, 不綁桌)",
)
async def takeout_sale(
    payload: TakeoutSaleRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CheckoutResult:
    return await pos_order_service.takeout_sale(session, payload, tenant_id=tenant_id)


@router.get(
    "/sessions/{session_id}/quote",
    response_model=CheckoutQuote,
    summary="應收金額 (折扣已計) — 結帳畫面用",
)
async def get_quote(
    session_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> CheckoutQuote:
    return await pos_order_service.quote(session, session_id, tenant_id=tenant_id)


@router.post(
    "/sessions/{session_id}/service-charge",
    response_model=CheckoutQuote,
    summary="設定服務費率 (0.1=10%; 0=移除)",
)
async def set_service_charge(
    session_id: uuid.UUID,
    payload: ServiceChargeRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CheckoutQuote:
    return await pos_order_service.set_service_charge(
        session, session_id, payload, tenant_id=tenant_id
    )


@router.post(
    "/sessions/{session_id}/pay",
    response_model=PartialPayResult,
    summary="拆單/分開結帳 — 收部分款項, 收齊自動結單結桌",
)
async def pay_partial(
    session_id: uuid.UUID,
    payload: PartialPayRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> PartialPayResult:
    return await pos_order_service.pay_partial(
        session, session_id, payload, tenant_id=tenant_id
    )


@router.post(
    "/sessions/{session_id}/checkout",
    response_model=CheckoutResult,
    summary="現金結帳 (結單 + 結桌 + 找零)",
)
async def checkout_cash(
    session_id: uuid.UUID,
    payload: CheckoutCashRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CheckoutResult:
    return await pos_order_service.checkout_cash(
        session, session_id, payload, tenant_id=tenant_id
    )


# ── Real-time floor events (P1.5) ────────────────────────────────────────────


@router.get(
    "/events",
    response_model=list[OrderEventResponse],
    summary="桌況事件游標補拉 (WS 的降級/補洞路徑)",
)
async def get_events(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
    after: int = _Q_AFTER,
) -> list[dict]:
    """Return every floor event for a store with ``seq > after``.

    Tablets normally receive these live over ``/tables/ws``; this REST cursor is
    the reconnect back-fill and the fallback when a WebSocket can't be held.
    """
    return await event_hub.fetch_events_since(
        session, tenant_id=tenant_id, store_id=store_id, after_seq=after
    )


def _resolve_ws_tenant(websocket: WebSocket) -> uuid.UUID:
    """Tenant for a WS connection.

    Mirrors ``get_current_tenant_id``: Phase 1 (multi-tenant off) always uses the
    default tenant; when enabled, the ``tenant_id`` query param or ``X-Tenant-Id``
    header drives it. WebSocket dependencies can't use the header DI cleanly, so
    we resolve here.
    """
    settings = get_settings()
    raw = settings.default_tenant_id
    if settings.multi_tenant_enabled:
        raw = (
            websocket.query_params.get("tenant_id")
            or websocket.headers.get("x-tenant-id")
            or settings.default_tenant_id
        )
    return uuid.UUID(raw)


@router.websocket("/ws")
async def floor_ws(
    websocket: WebSocket,
    store_id: uuid.UUID,
    after: int = 0,
) -> None:
    """Live floor stream for one store's tablets.

    Protocol:
      1. Client connects ``/tables/ws?store_id=<uuid>&after=<last_seq>``.
      2. Server replays every missed event (``seq > after``) from the durable
         log, then streams new events live.
      3. Each frame carries a monotonic ``seq``; the client stores the highest
         it has applied and ignores anything ``<=`` it, so the subscribe-then-
         replay overlap can double-send without the client double-applying —
         no gaps, no duplicated effects.

    Heartbeat ``{"type":"ping"}`` frames keep idle sockets alive and surface a
    dead peer on the next send.
    """
    await websocket.accept()
    tenant_id = _resolve_ws_tenant(websocket)
    hub = event_hub.get_hub()
    # Subscribe BEFORE the catch-up read so an event committed during replay
    # lands in the queue rather than being missed (client dedupes by seq).
    queue = hub.subscribe(store_id)
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            missed = await event_hub.fetch_events_since(
                s, tenant_id=tenant_id, store_id=store_id, after_seq=after
            )
        for ev in missed:
            await websocket.send_json(ev)

        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
                await websocket.send_json(ev)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # socket died mid-send; just clean up
        logger.info("floor_ws.closed", extra={"store_id": str(store_id), "reason": str(exc)})
    finally:
        hub.unsubscribe(store_id, queue)


__all__ = ["router"]
