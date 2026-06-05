"""Pydantic v2 schemas for the ``/reservations`` and ``/queue`` routers.

Two distinct lifecycle objects share this module because they're conceptually
the same front-of-house flow seen from different starting points:

  pre-booked → ``Reservation`` (BOOKED → CONFIRMED → SEATED → COMPLETED)
  walk-in    → ``WalkInQueueEntry`` (WAITING → CALLED → SEATED)

The router validates incoming state transitions against
``RESERVATION_TRANSITIONS`` / ``QUEUE_TRANSITIONS`` below. Invalid hops
raise ``ConflictError``; the rules are the source of truth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import QueueStatus, ReservationStatus

# ──────────────────────────────────────────────────────────────────────────
# Transition graphs — single source of truth shared between router + tests.
# ──────────────────────────────────────────────────────────────────────────


RESERVATION_TRANSITIONS: dict[ReservationStatus, set[ReservationStatus]] = {
    ReservationStatus.BOOKED: {
        ReservationStatus.CONFIRMED,
        ReservationStatus.SEATED,  # walk-in arrived without confirming first
        ReservationStatus.NO_SHOW,
        ReservationStatus.CANCELLED,
    },
    ReservationStatus.CONFIRMED: {
        ReservationStatus.SEATED,
        ReservationStatus.NO_SHOW,
        ReservationStatus.CANCELLED,
    },
    ReservationStatus.SEATED: {
        ReservationStatus.COMPLETED,
    },
    # Terminal states — empty set means "no further transitions allowed"
    ReservationStatus.COMPLETED: set(),
    ReservationStatus.NO_SHOW: set(),
    ReservationStatus.CANCELLED: set(),
}


QUEUE_TRANSITIONS: dict[QueueStatus, set[QueueStatus]] = {
    QueueStatus.WAITING: {QueueStatus.CALLED, QueueStatus.ABANDONED},
    QueueStatus.CALLED: {QueueStatus.SEATED, QueueStatus.ABANDONED},
    QueueStatus.SEATED: set(),
    QueueStatus.ABANDONED: set(),
}


# ──────────────────────────────────────────────────────────────────────────
# Reservation — create / patch / response
# ──────────────────────────────────────────────────────────────────────────


# Channel strings stay open because new ones land monthly (eztable, google
# reserve, inline, ...) — Literal would force a migration for every new feed.
ReservationSource = Annotated[str, Field(min_length=1, max_length=32)]


class ReservationCreate(BaseModel):
    """Create a new booking. Status always starts as ``booked``."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    customer_id: UUID | None = None
    contact_name: str = Field(min_length=1, max_length=80)
    contact_phone: str | None = Field(default=None, max_length=32)
    party_size: int = Field(gt=0, le=200)
    reserved_for: datetime
    duration_minutes: int | None = Field(default=None, gt=0, le=600)
    source: ReservationSource = "phone"
    deposit_amount: int | None = Field(default=None, ge=0)
    notes: str | None = None

    @model_validator(mode="after")
    def _reserved_for_tz_aware(self) -> ReservationCreate:
        if self.reserved_for.tzinfo is None:
            raise ValueError("reserved_for must be timezone-aware")
        return self


class ReservationStatusPatch(BaseModel):
    """Transition a reservation to a new status. The router enforces
    ``RESERVATION_TRANSITIONS`` — any disallowed hop returns 409."""

    model_config = ConfigDict(frozen=True)

    status: ReservationStatus
    # Optional free-text reason — recorded in the audit_log row.
    reason: str | None = Field(default=None, max_length=500)
    # Only used when transitioning to SEATED — defaults to ``now()``.
    arrived_at: datetime | None = None
    actor_id: UUID | None = None

    @model_validator(mode="after")
    def _arrived_at_tz_aware(self) -> ReservationStatusPatch:
        if self.arrived_at is not None and self.arrived_at.tzinfo is None:
            raise ValueError("arrived_at must be timezone-aware")
        return self


class ReservationResponse(BaseModel):
    """Reservation row projected for HTTP."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID | None
    contact_name: str
    contact_phone: str | None
    party_size: int
    reserved_for: datetime
    duration_minutes: int | None
    status: ReservationStatus
    source: str
    deposit_amount: int | None
    notes: str | None
    arrived_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Walk-in queue — create / patch / response
# ──────────────────────────────────────────────────────────────────────────


class QueueJoinRequest(BaseModel):
    """A walk-in party joins the queue."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    customer_id: UUID | None = None
    contact_name: str = Field(min_length=1, max_length=80)
    contact_phone: str | None = Field(default=None, max_length=32)
    party_size: int = Field(gt=0, le=200)
    queue_no: str | None = Field(default=None, max_length=16)
    notes: str | None = None


class QueueStatusPatch(BaseModel):
    """Transition a queue entry. Router enforces ``QUEUE_TRANSITIONS``."""

    model_config = ConfigDict(frozen=True)

    status: QueueStatus
    reason: str | None = Field(default=None, max_length=500)
    actor_id: UUID | None = None


class QueueEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID | None
    contact_name: str
    contact_phone: str | None
    party_size: int
    queue_no: str | None
    status: QueueStatus
    joined_at: datetime
    called_at: datetime | None
    seated_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


# Re-export status literals so router signatures stay readable.
ReservationStatusLiteral = Literal[
    "booked", "confirmed", "seated", "completed", "no_show", "cancelled"
]
QueueStatusLiteral = Literal["waiting", "called", "seated", "abandoned"]


__all__ = [
    "QUEUE_TRANSITIONS",
    "RESERVATION_TRANSITIONS",
    "QueueEntryResponse",
    "QueueJoinRequest",
    "QueueStatusLiteral",
    "QueueStatusPatch",
    "ReservationCreate",
    "ReservationResponse",
    "ReservationSource",
    "ReservationStatusLiteral",
    "ReservationStatusPatch",
]
