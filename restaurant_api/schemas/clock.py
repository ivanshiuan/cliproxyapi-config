"""Pydantic v2 schemas for the ``/clock`` router.

Mirrors the contract in ``specs/clock_router.md``. Input models are
``frozen=True, extra="forbid"``. We deliberately don't enable
``strict=True`` at model level because pydantic v2 strict mode rejects
JSON-string UUIDs (and FastAPI body parsing turns JSON into a dict before
validation, so it sees ``str`` not the original JSON token). Instead we
hand-roll the strictness that actually matters for this domain:

- tz-aware datetimes only (``_ensure_tz_aware`` field validator).
- floats rejected for ``hours`` (so callers can't sneak in a binary
  fraction that looks fine but rounds wrong against the DB ``Numeric``).
- unknown fields rejected (``extra="forbid"``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Closed sets ----------------------------------------------------------------

ClockSource = Literal["manual", "qr", "face", "gps"]
LeaveTypeStr = Literal[
    "特休", "事假", "病假", "婚假", "喪假", "產假", "生理假", "公假", "其他",
]
LeaveStatusStr = Literal["pending", "approved", "rejected", "cancelled"]


def _ensure_tz_aware(value: datetime, field_name: str) -> datetime:
    """Reject tz-naive datetimes — the entire stack runs in UTC."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware (UTC or with offset)")
    return value


# Requests -------------------------------------------------------------------


class ClockInRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: UUID
    store_id: UUID
    clock_in: datetime | None = None
    source: ClockSource = "manual"
    notes: str | None = Field(default=None, max_length=200)

    @field_validator("clock_in")
    @classmethod
    def _check_tz(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _ensure_tz_aware(v, "clock_in")


class ClockOutRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: UUID
    clock_out: datetime | None = None
    notes: str | None = Field(default=None, max_length=200)

    @field_validator("clock_out")
    @classmethod
    def _check_tz(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _ensure_tz_aware(v, "clock_out")


class LeaveRequestCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    employee_id: UUID
    leave_type: LeaveTypeStr
    start_at: datetime
    end_at: datetime
    # ``hours`` is optional — the service computes hours = (end - start) when
    # the caller doesn't provide its own (e.g. half-day leave with custom value).
    hours: Decimal | None = Field(default=None, gt=Decimal("0"))
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("start_at", "end_at")
    @classmethod
    def _check_tz(cls, v: datetime) -> datetime:
        return _ensure_tz_aware(v, "start_at/end_at")

    @field_validator("hours", mode="before")
    @classmethod
    def _reject_float_hours(cls, v: object) -> object:
        """Reject ``float`` inputs (JSON ``1.5``) — clients must send a
        string (``"1.50"``) or omit and let the service compute it.

        This avoids the well-known IEEE-754 → Decimal trap where
        ``Decimal(8.1) == Decimal('8.0999999...')``.
        """
        if isinstance(v, float):
            raise ValueError("hours must be a Decimal string, not float")
        return v

    @model_validator(mode="after")
    def _check_window(self) -> LeaveRequestCreate:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be > start_at")
        return self


# Responses ------------------------------------------------------------------


class ClockInResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time_clock_id: UUID
    employee_id: UUID
    store_id: UUID
    clock_in: datetime


class ClockOutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time_clock_id: UUID
    employee_id: UUID
    clock_in: datetime
    clock_out: datetime
    regular_hours: Decimal
    overtime_tier1_hours: Decimal
    overtime_tier2_hours: Decimal
    holiday_hours: Decimal
    total_hours: Decimal


class LeaveRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    leave_type: str
    start_at: datetime
    end_at: datetime
    hours: Decimal
    status: LeaveStatusStr
    reason: str | None


class OpenClockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time_clock_id: UUID
    employee_id: UUID
    employee_name: str
    store_id: UUID
    clock_in: datetime
    elapsed_hours: Decimal


__all__ = [
    "ClockInRequest",
    "ClockInResponse",
    "ClockOutRequest",
    "ClockOutResponse",
    "ClockSource",
    "LeaveRequestCreate",
    "LeaveRequestResponse",
    "LeaveStatusStr",
    "LeaveTypeStr",
    "OpenClockResponse",
]
