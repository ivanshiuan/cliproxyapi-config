"""Business logic for the ``/clock`` router.

Keeps the router thin — every domain rule (open-clock detection, hour
bucketing, leave-request defaults) lives here so it's unit-testable
without spinning up FastAPI.

NOTE — labor classifier
=======================
This module ships with a *stub* implementation of the LSA (Taiwan
勞動基準法) hour-bucket classifier:

    regular ≤ 8h  →  +OT-tier1 ≤ 2h  →  +OT-tier2 ≤ 2h  →  raise > 12h

DevSwarm will ship the production-grade ``labor_hours_classifier`` later
(see ``specs/calc_engine.md``). When that lands, swap the body of
``classify_hours`` for a call into it — the signature is intentionally
identical so the call site in :func:`clock_out` doesn't have to change.

Similarly, holidays default to "weekend in Asia/Taipei". A real
``tw_holidays`` table will replace :func:`default_is_holiday` — both are
exposed via FastAPI ``Depends`` overrides so tests (and future DI swaps)
don't need to monkey-patch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import Employee, LeaveRequest, LeaveStatus, LeaveType, TimeClock
from ..schemas.clock import (
    ClockInRequest,
    ClockInResponse,
    ClockOutRequest,
    ClockOutResponse,
    LeaveRequestCreate,
    LeaveRequestResponse,
    OpenClockResponse,
)

TPE_TZ = ZoneInfo("Asia/Taipei")

_TWO_DP = Decimal("0.01")
_EIGHT = Decimal("8")
_TWO = Decimal("2")
_TWELVE = Decimal("12")
_CLOCK_FUTURE_TOLERANCE = timedelta(seconds=60)


# ──────────────────────────────────────────────────────────────────────────
# Stubs — replaced by DevSwarm-provided real implementations later
# ──────────────────────────────────────────────────────────────────────────


class IsHolidayFn(Protocol):
    """Type seam for the holiday predicate.

    Real impl will look up the ``tw_holidays`` table; MVP defaults to
    "weekend in Asia/Taipei".
    """

    def __call__(self, d: date) -> bool: ...


def default_is_holiday(d: date) -> bool:
    """MVP holiday rule: Saturday or Sunday (Asia/Taipei weekdays 5, 6).

    TODO(devswarm): replace with a lookup against ``tw_holidays`` table
    once the calendar seed exists.
    """
    return d.weekday() in (5, 6)


def classify_hours(
    clock_in: datetime,
    clock_out: datetime,
    *,
    is_holiday: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Split worked hours into the four 勞基法 buckets.

    Thin adapter around ``calc.labor_hours_classifier.classify_hours`` —
    callers keep the (clock_in, clock_out, is_holiday) tuple signature
    they had with the stub; the real engine receives a LaborInput
    Pydantic model with public_holidays list.

    Rules:
      * is_holiday=True  → every hour into ``holiday_hours``.
      * is_holiday=False → first 8h regular, next 2h OT-tier1, next 2h
        OT-tier2, > 12h raises ValidationError.

    Returns a 4-tuple of Decimals (regular, ot1, ot2, holiday), 2 d.p.
    """
    from .calc.labor_hours_classifier import (
        LaborInput as _CalcLaborInput,
    )
    from .calc.labor_hours_classifier import (
        classify_hours as _calc_classify,
    )

    if clock_out <= clock_in:
        raise ValidationError(
            "clock_out must be after clock_in",
            details={"code": "clock_order_violation"},
        )

    # The real engine reads its holiday status from ``public_holidays``;
    # we synthesise a one-element list when the caller's stub says holiday.
    holiday_dates = [clock_in.date()] if is_holiday else []
    try:
        buckets = _calc_classify(
            _CalcLaborInput(
                employee_id="adapter",  # not used in buckets math
                clock_in=clock_in,
                clock_out=clock_out,
                public_holidays=holiday_dates,
            )
        )
    except ValueError as e:
        # Real engine raises ValueError on > 12h / naive datetime; surface as
        # our typed domain error for the FastAPI handler.
        raise ValidationError(
            str(e),
            details={"code": "hours_over_cap"},
        ) from e

    return (
        buckets.regular_hours,
        buckets.overtime_tier1_hours,
        buckets.overtime_tier2_hours,
        buckets.holiday_hours,
    )


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    """tz-aware UTC ``now()`` — keeps a single seam for test freezing."""
    return datetime.now(tz=UTC)


def _to_tpe(dt: datetime) -> datetime:
    """Convert a tz-aware datetime to Asia/Taipei for API responses."""
    return dt.astimezone(TPE_TZ)


async def _load_employee(session: AsyncSession, employee_id: uuid.UUID) -> Employee:
    """Fetch an active, non-terminated employee or raise 404."""
    emp = await session.get(Employee, employee_id)
    if emp is None or not emp.is_active:
        raise NotFoundError("employee not found", details={"employee_id": str(employee_id)})
    today = _utcnow().date()
    if emp.terminated_on is not None and emp.terminated_on <= today:
        raise NotFoundError("employee not found", details={"employee_id": str(employee_id)})
    if emp.deleted_at is not None:
        raise NotFoundError("employee not found", details={"employee_id": str(employee_id)})
    return emp


async def _find_open_clock(
    session: AsyncSession, employee_id: uuid.UUID
) -> TimeClock | None:
    """Return the most recent open ``time_clocks`` row for an employee."""
    stmt = (
        select(TimeClock)
        .where(TimeClock.employee_id == employee_id, TimeClock.clock_out.is_(None))
        .order_by(TimeClock.clock_in.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ──────────────────────────────────────────────────────────────────────────
# Service entrypoints — one per route
# ──────────────────────────────────────────────────────────────────────────


async def clock_in(
    session: AsyncSession,
    payload: ClockInRequest,
) -> ClockInResponse:
    """Open a ``time_clocks`` row. Idempotency: 409 if one's already open."""
    emp = await _load_employee(session, payload.employee_id)

    now = _utcnow()
    when = payload.clock_in or now
    if when - now > _CLOCK_FUTURE_TOLERANCE:
        raise ValidationError(
            "clock_in cannot be in the future",
            details={"code": "clock_future"},
        )

    existing = await _find_open_clock(session, emp.id)
    if existing is not None:
        raise ConflictError(
            "employee already clocked in",
            details={
                "code": "already_clocked_in",
                "time_clock_id": str(existing.id),
                "clock_in_at": existing.clock_in.isoformat(),
            },
        )

    row = TimeClock(
        tenant_id=emp.tenant_id,
        store_id=payload.store_id,
        employee_id=emp.id,
        clock_in=when.astimezone(UTC),
        clock_out=None,
        regular_hours=Decimal("0"),
        overtime_tier1_hours=Decimal("0"),
        overtime_tier2_hours=Decimal("0"),
        holiday_hours=Decimal("0"),
        notes=_compose_notes(payload.notes, source=payload.source),
    )
    session.add(row)
    await session.flush()

    return ClockInResponse(
        time_clock_id=row.id,
        employee_id=row.employee_id,
        store_id=row.store_id,
        clock_in=_to_tpe(row.clock_in),
    )


async def clock_out(
    session: AsyncSession,
    payload: ClockOutRequest,
    *,
    is_holiday: IsHolidayFn,
) -> ClockOutResponse:
    """Close the employee's open clock row and bucket the hours."""
    await _load_employee(session, payload.employee_id)

    open_row = await _find_open_clock(session, payload.employee_id)
    if open_row is None:
        raise ConflictError(
            "employee is not clocked in",
            details={"code": "not_clocked_in"},
        )

    now = _utcnow()
    when = (payload.clock_out or now).astimezone(UTC)
    if when <= open_row.clock_in:
        raise ValidationError(
            "clock_out must be after clock_in",
            details={"code": "clock_order_violation"},
        )

    # Holiday classification keys off the *clock_in* TPE date — a graveyard
    # shift that starts Friday and ends Saturday still books the whole
    # block at "Friday rates".
    in_date_tpe = _to_tpe(open_row.clock_in).date()
    regular, ot1, ot2, holiday = classify_hours(
        open_row.clock_in,
        when,
        is_holiday=is_holiday(in_date_tpe),
    )

    open_row.clock_out = when
    open_row.regular_hours = regular
    open_row.overtime_tier1_hours = ot1
    open_row.overtime_tier2_hours = ot2
    open_row.holiday_hours = holiday
    if payload.notes:
        open_row.notes = _append_notes(open_row.notes, payload.notes)
    await session.flush()

    total = (regular + ot1 + ot2 + holiday).quantize(_TWO_DP)
    return ClockOutResponse(
        time_clock_id=open_row.id,
        employee_id=open_row.employee_id,
        clock_in=_to_tpe(open_row.clock_in),
        clock_out=_to_tpe(when),
        regular_hours=regular,
        overtime_tier1_hours=ot1,
        overtime_tier2_hours=ot2,
        holiday_hours=holiday,
        total_hours=total,
    )


async def create_leave_request(
    session: AsyncSession,
    payload: LeaveRequestCreate,
) -> LeaveRequestResponse:
    """INSERT a ``leave_requests`` row with status=pending."""
    emp = await _load_employee(session, payload.employee_id)

    if payload.hours is None:
        delta = payload.end_at - payload.start_at
        hours = (
            Decimal(str(delta.total_seconds())) / Decimal("3600")
        ).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
        if hours <= Decimal("0"):
            raise ValidationError("computed hours must be > 0")
    else:
        hours = payload.hours.quantize(_TWO_DP, rounding=ROUND_HALF_UP)

    row = LeaveRequest(
        tenant_id=emp.tenant_id,
        employee_id=emp.id,
        leave_type=LeaveType(payload.leave_type),
        start_at=payload.start_at.astimezone(UTC),
        end_at=payload.end_at.astimezone(UTC),
        hours=hours,
        status=LeaveStatus.PENDING,
        reason=payload.reason,
    )
    session.add(row)
    await session.flush()

    return LeaveRequestResponse(
        id=row.id,
        employee_id=row.employee_id,
        leave_type=str(row.leave_type.value),
        start_at=_to_tpe(row.start_at),
        end_at=_to_tpe(row.end_at),
        hours=row.hours,
        status="pending",
        reason=row.reason,
    )


async def list_today_open_clocks(
    session: AsyncSession,
    store_id: uuid.UUID | None,
) -> list[OpenClockResponse]:
    """Return every still-open time_clock whose clock_in date (TPE) = today."""
    today_tpe = _utcnow().astimezone(TPE_TZ).date()

    stmt = (
        select(TimeClock, Employee)
        .join(Employee, Employee.id == TimeClock.employee_id)
        .where(TimeClock.clock_out.is_(None))
    )
    if store_id is not None:
        stmt = stmt.where(TimeClock.store_id == store_id)

    rows = (await session.execute(stmt)).all()
    now = _utcnow()
    out: list[OpenClockResponse] = []
    for tc, emp in rows:
        if _to_tpe(tc.clock_in).date() != today_tpe:
            continue
        elapsed = (
            Decimal(str((now - tc.clock_in).total_seconds())) / Decimal("3600")
        ).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
        out.append(
            OpenClockResponse(
                time_clock_id=tc.id,
                employee_id=tc.employee_id,
                employee_name=emp.full_name,
                store_id=tc.store_id,
                clock_in=_to_tpe(tc.clock_in),
                elapsed_hours=elapsed,
            )
        )
    return out


# ──────────────────────────────────────────────────────────────────────────
# Notes helpers — ``source`` is dumped into the free-form notes column,
# keeping the schema small while preserving audit info.
# ──────────────────────────────────────────────────────────────────────────


def _compose_notes(notes: str | None, *, source: str) -> str:
    """Prefix notes with the clock-in source tag."""
    tag = f"[source={source}]"
    return tag if not notes else f"{tag} {notes}"


def _append_notes(existing: str | None, addition: str) -> str:
    if not existing:
        return addition
    return f"{existing} | {addition}"


__all__ = [
    "IsHolidayFn",
    "classify_hours",
    "clock_in",
    "clock_out",
    "create_leave_request",
    "default_is_holiday",
    "list_today_open_clocks",
]
