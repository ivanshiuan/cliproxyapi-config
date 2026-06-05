"""FastAPI router — ``/kitchen`` (KDS poll + state transitions).

Two endpoints, both thin over ``services/kitchen_service``:

  GET    /kitchen/queue           — poll endpoint for the KDS UI
  PATCH  /kitchen/lines/{id}/status  — transition a line through the
                                       queued → cooking → ready → served
                                       lifecycle

The orders router stamps ``kitchen_station=<station>`` +
``kitchen_status=queued`` at line-creation time when the caller routed
the line to KDS. This router is what the kitchen tablet talks to once
the line is on the board.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
from ..models import KitchenStation, KitchenStatus
from ..schemas.kitchen import KitchenLineResponse, KitchenStatusPatch
from ..services import kitchen_service

# Module-level Query() singletons — ruff B008 avoidance.
_Q_STORE_ID = Query(default=None)
_Q_STATION = Query(default=None)
_Q_STATUS = Query(default=None)
_Q_LIMIT = Query(default=200, ge=1, le=500)


router = APIRouter(prefix="/kitchen", tags=["kitchen"])


@router.get(
    "/queue",
    response_model=list[KitchenLineResponse],
    summary="KDS poll endpoint — list lines on the board (oldest first)",
)
async def list_kitchen_queue(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    station: KitchenStation | None = _Q_STATION,
    kitchen_status: KitchenStatus | None = _Q_STATUS,
    limit: int = _Q_LIMIT,
) -> list[KitchenLineResponse]:
    return await kitchen_service.list_queue(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        station=station,
        status=kitchen_status,
        limit=limit,
    )


@router.patch(
    "/lines/{line_id}/status",
    response_model=KitchenLineResponse,
    status_code=status.HTTP_200_OK,
    summary="更新廚房線狀態 (cook / ready / serve / cancel)",
)
async def patch_kitchen_line_status(
    line_id: uuid.UUID,
    payload: KitchenStatusPatch,
    session: DbSession,
    tenant_id: TenantId,
) -> KitchenLineResponse:
    return await kitchen_service.patch_line_status(
        session, line_id, payload, tenant_id=tenant_id
    )


__all__ = ["router"]
