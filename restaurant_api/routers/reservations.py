"""FastAPI router — ``/reservations`` and ``/queue`` (front-of-house).

Two state machines (Reservation, WalkInQueueEntry) — one APIRouter per
prefix so the OpenAPI groupings stay clean. Both delegate to
``services/reservation_service`` for the actual logic; the router is
the thin parse-validate-respond layer.

State transitions are enforced server-side. The full transition graph
lives in ``schemas/reservations.RESERVATION_TRANSITIONS`` and
``QUEUE_TRANSITIONS`` — try a disallowed hop and you get a 409 with the
list of permitted next states in the error envelope.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
from ..models import QueueStatus, ReservationStatus
from ..schemas.reservations import (
    QueueEntryResponse,
    QueueJoinRequest,
    QueueStatusPatch,
    ReservationCreate,
    ReservationResponse,
    ReservationStatusPatch,
)
from ..services import reservation_service

# Module-level Query() singletons (ruff B008 avoidance, FastAPI metadata kept).
_Q_STORE_ID = Query(default=None)
_Q_RES_STATUS = Query(default=None)
_Q_QUEUE_STATUS = Query(default=None)
_Q_FROM_DT = Query(default=None)
_Q_TO_DT = Query(default=None)
_Q_LIMIT = Query(default=200, ge=1, le=500)


# ──────────────────────────────────────────────────────────────────────────
# /reservations
# ──────────────────────────────────────────────────────────────────────────

reservations_router = APIRouter(prefix="/reservations", tags=["reservations"])


@reservations_router.post(
    "",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立訂位 (booking starts in status=booked)",
)
async def create_reservation(
    payload: ReservationCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> ReservationResponse:
    return await reservation_service.create_reservation(
        session, payload, tenant_id=tenant_id
    )


@reservations_router.get(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="查詢單筆訂位",
)
async def get_reservation(
    reservation_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> ReservationResponse:
    return await reservation_service.get_reservation(
        session, reservation_id, tenant_id=tenant_id
    )


@reservations_router.get(
    "",
    response_model=list[ReservationResponse],
    summary="列出訂位 (依 store / status / 時間區間過濾)",
)
async def list_reservations(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    res_status: ReservationStatus | None = _Q_RES_STATUS,
    from_dt: datetime | None = _Q_FROM_DT,
    to_dt: datetime | None = _Q_TO_DT,
    limit: int = _Q_LIMIT,
) -> list[ReservationResponse]:
    return await reservation_service.list_reservations(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        status=res_status,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
    )


@reservations_router.patch(
    "/{reservation_id}/status",
    response_model=ReservationResponse,
    summary="更新訂位狀態 (confirm/seat/complete/no_show/cancel)",
)
async def patch_reservation_status(
    reservation_id: uuid.UUID,
    payload: ReservationStatusPatch,
    session: DbSession,
    tenant_id: TenantId,
) -> ReservationResponse:
    return await reservation_service.patch_reservation_status(
        session, reservation_id, payload, tenant_id=tenant_id
    )


# ──────────────────────────────────────────────────────────────────────────
# /queue (walk-in)
# ──────────────────────────────────────────────────────────────────────────

queue_router = APIRouter(prefix="/queue", tags=["queue"])


@queue_router.post(
    "",
    response_model=QueueEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="加入現場候位 (status=waiting)",
)
async def join_queue(
    payload: QueueJoinRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> QueueEntryResponse:
    return await reservation_service.join_queue(
        session, payload, tenant_id=tenant_id
    )


@queue_router.get(
    "",
    response_model=list[QueueEntryResponse],
    summary="列出候位 (依 store / status 過濾)",
)
async def list_queue(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    queue_status: QueueStatus | None = _Q_QUEUE_STATUS,
    limit: int = _Q_LIMIT,
) -> list[QueueEntryResponse]:
    return await reservation_service.list_queue(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        status=queue_status,
        limit=limit,
    )


@queue_router.patch(
    "/{queue_id}/status",
    response_model=QueueEntryResponse,
    summary="更新候位狀態 (call/seat/abandon)",
)
async def patch_queue_status(
    queue_id: uuid.UUID,
    payload: QueueStatusPatch,
    session: DbSession,
    tenant_id: TenantId,
) -> QueueEntryResponse:
    return await reservation_service.patch_queue_status(
        session, queue_id, payload, tenant_id=tenant_id
    )


# Module's public surface — main.py imports these.
__all__ = ["queue_router", "reservations_router"]
