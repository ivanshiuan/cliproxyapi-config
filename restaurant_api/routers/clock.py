"""FastAPI router — ``/clock`` (HR time tracking + leave requests).

Thin layer: parses the request, hands it to ``clock_service``, returns the
response model. No business logic lives here — every rule (open-clock
conflict, hour bucketing, holiday handling) is in
``restaurant_api.services.clock_service`` so the router stays
substitutable.

DI seams:
- ``DbSession``: async SQLAlchemy session.
- ``get_is_holiday``: returns a predicate ``(date) -> bool``. Default uses
  the weekday-based MVP rule; tests / future ``tw_holidays`` lookups
  override via ``app.dependency_overrides``.

NOTE: This router is *not yet* wired into ``restaurant_api.main:app``
(file ownership boundary — main.py wiring lands in a separate change).
The test suite mounts it explicitly via an autouse fixture.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ..api.deps import DbSession
from ..schemas.clock import (
    ClockInRequest,
    ClockInResponse,
    ClockOutRequest,
    ClockOutResponse,
    LeaveRequestCreate,
    LeaveRequestResponse,
    OpenClockResponse,
)
from ..services import clock_service
from ..services.clock_service import IsHolidayFn, default_is_holiday

router = APIRouter(prefix="/clock", tags=["clock"])


def get_is_holiday() -> IsHolidayFn:
    """DI seam for the holiday predicate.

    Default: weekday-based "weekend = holiday" rule. Tests override via
    ``app.dependency_overrides[get_is_holiday] = lambda: lambda d: True``.
    """
    return default_is_holiday


IsHolidayDep = Annotated[IsHolidayFn, Depends(get_is_holiday)]


@router.post(
    "/in",
    response_model=ClockInResponse,
    status_code=status.HTTP_201_CREATED,
    summary="員工打卡上班",
)
async def post_clock_in(
    payload: ClockInRequest,
    session: DbSession,
) -> ClockInResponse:
    return await clock_service.clock_in(session, payload)


@router.post(
    "/out",
    response_model=ClockOutResponse,
    status_code=status.HTTP_200_OK,
    summary="員工打卡下班 (calculates LSA hour buckets)",
)
async def post_clock_out(
    payload: ClockOutRequest,
    session: DbSession,
    is_holiday: IsHolidayDep,
) -> ClockOutResponse:
    return await clock_service.clock_out(session, payload, is_holiday=is_holiday)


@router.post(
    "/leave-request",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="員工提出請假申請 (status=pending)",
)
async def post_leave_request(
    payload: LeaveRequestCreate,
    session: DbSession,
) -> LeaveRequestResponse:
    return await clock_service.create_leave_request(session, payload)


@router.get(
    "/today",
    response_model=list[OpenClockResponse],
    summary="今日仍在班上 (clock_out IS NULL) — by Asia/Taipei date",
)
async def get_today_open(
    session: DbSession,
    store_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[OpenClockResponse]:
    return await clock_service.list_today_open_clocks(session, store_id)


__all__ = ["get_is_holiday", "router"]
