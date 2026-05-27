"""FastAPI router — ``/events`` (waste / staff_meal / tasting).

Three POST endpoints (one per event type), one GET endpoint for unified
listing across the three tables.

Immutability
------------
There are **no** PATCH / PUT / DELETE endpoints in this module by design —
the three event types are append-only at the API layer. Fix data errors by
appending a reversal event via the ``stock_movements`` adjustment flow
(separate router). The OpenAPI immutability test asserts this.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from ..api.deps import DbSession
from ..schemas.events import (
    EventListItem,
    StaffMealEventCreate,
    StaffMealEventResponse,
    TastingEventCreate,
    TastingEventResponse,
    WasteEventCreate,
    WasteEventResponse,
)
from ..services import events_service

# Module-level Query() singletons — keeps ruff B008 happy without sacrificing
# the per-field validation metadata FastAPI extracts from them.
_Q_STORE_ID = Query(default=None)
_Q_EVENT_TYPE = Query(default=None)
_Q_FROM_DATE = Query(default=None)
_Q_TO_DATE = Query(default=None)
_Q_LIMIT = Query(default=200, ge=1, le=500)

router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "/waste",
    response_model=WasteEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a 報廢 (waste) event",
)
async def create_waste(
    payload: WasteEventCreate,
    session: DbSession,
) -> JSONResponse:
    response, created = await events_service.create_waste_event(session, payload)
    await session.commit()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/staff-meal",
    response_model=StaffMealEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an 員工餐 (staff meal) event",
)
async def create_staff_meal(
    payload: StaffMealEventCreate,
    session: DbSession,
) -> JSONResponse:
    response, created = await events_service.create_staff_meal_event(session, payload)
    await session.commit()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/tasting",
    response_model=TastingEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a 試吃 / 試菜 (tasting) event",
)
async def create_tasting(
    payload: TastingEventCreate,
    session: DbSession,
) -> JSONResponse:
    response, created = await events_service.create_tasting_event(session, payload)
    await session.commit()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )


@router.get(
    "",
    response_model=list[EventListItem],
    summary="List cost events with filters",
)
async def list_events_endpoint(
    session: DbSession,
    store_id: UUID | None = _Q_STORE_ID,
    event_type: Literal["waste", "staff_meal", "tasting"] | None = _Q_EVENT_TYPE,
    from_date: date | None = _Q_FROM_DATE,
    to_date: date | None = _Q_TO_DATE,
    limit: int = _Q_LIMIT,
) -> list[EventListItem]:
    return await events_service.list_events(
        session,
        store_id=store_id,
        event_type=event_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
    )


__all__ = ["router"]
