"""FastAPI router — ``/tables`` (POS floor plan + table sessions, P1.3).

Thin layer over ``services/table_service``. Two resource families under one
prefix: physical tables (CRUD) and their seatings (open/close/transfer +
the floor-plan board).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
from ..schemas.tables import (
    DiningTableCreate,
    DiningTableResponse,
    DiningTableUpdate,
    FloorTableView,
    TableSessionOpen,
    TableSessionResponse,
    TableSessionTransfer,
)
from ..services import table_service

_Q_STORE_ID = Query(default=None)
_Q_STORE_ID_REQ = Query()
_Q_ACTOR_ID = Query(default=None)

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


__all__ = ["router"]
