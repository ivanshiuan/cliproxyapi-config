"""Reservations + walk-in queueing — Taiwan F&B SOP front half.

The full service flow is:
    候位 (queue) ─► 訂位 (reservation) ─► 入座 (seat) ─► 點餐 (order)
                                                ▲
                                    Phase 1 starts here; this module
                                    covers the two stages before it.

This file is wired schema-only in Phase 1; the LINE-bot booking flow and
the in-store kiosk for self-check-in land in Phase 2.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, TimestampedMixin, uuid7

if TYPE_CHECKING:  # avoid runtime circular import; only for type hints
    pass


class ReservationStatus(enum.StrEnum):
    """Booking lifecycle.

    Transitions:
        booked → confirmed → seated → completed
                                  ↓
                              no_show  (auto after 15 min past slot)
                                  ↓
                              cancelled (customer-initiated)
    """

    BOOKED = "booked"
    CONFIRMED = "confirmed"  # we sent a LINE/SMS confirmation and they replied
    SEATED = "seated"  # walked in, took table
    COMPLETED = "completed"  # finished meal
    NO_SHOW = "no_show"  # did not arrive
    CANCELLED = "cancelled"


class Reservation(TenantScopedMixin, TimestampedMixin, Base):
    """Pre-arranged booking.

    ``customer_id`` is nullable because guests can book by phone without
    being a registered member (their phone number is the contact handle).
    """

    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # SET NULL: keep the booking record if the customer profile is removed.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Contact handles even if no customer profile attached.
    contact_name: Mapped[str] = mapped_column(String(80), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    # Optional 30/60/90/120 min buckets so the host knows when to clear the table.
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ReservationStatus] = mapped_column(
        SQLEnum(ReservationStatus, name="reservation_status", native_enum=False, length=16),
        nullable=False,
        default=ReservationStatus.BOOKED,
        server_default=ReservationStatus.BOOKED.value,
    )
    # Channel the booking came in through. Strings (not enum) because new
    # channels are added often: "phone", "line", "google_reserve", "inline",
    # "walk_in_promoted", "eztable", ...
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="phone")
    # Optional confirmation deposit (some 火鍋店 require 訂金).
    deposit_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the customer actually showed up (or we marked them no-show).
    arrived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_reservations_store_time", "store_id", "reserved_for"),
        Index("ix_reservations_status_time", "status", "reserved_for"),
    )


class QueueStatus(enum.StrEnum):
    """Walk-in queue lifecycle.

    Transitions:
        waiting → called → seated
              ↓
          abandoned (no-show after being called)
    """

    WAITING = "waiting"
    CALLED = "called"  # host called the party but they have a grace window
    SEATED = "seated"
    ABANDONED = "abandoned"  # did not respond to call / left voluntarily


class WalkInQueueEntry(TenantScopedMixin, TimestampedMixin, Base):
    """Walk-in (現場候位) — the queue you maintain on busy weekends."""

    __tablename__ = "walk_in_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contact_name: Mapped[str] = mapped_column(String(80), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # The host writes a short ticket number ("A-23") for customer hand-off.
    queue_no: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[QueueStatus] = mapped_column(
        SQLEnum(QueueStatus, name="queue_status", native_enum=False, length=16),
        nullable=False,
        default=QueueStatus.WAITING,
        server_default=QueueStatus.WAITING.value,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    called_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    seated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_queue_store_status", "store_id", "status", "joined_at"),
    )


__all__ = [
    "QueueStatus",
    "Reservation",
    "ReservationStatus",
    "WalkInQueueEntry",
]
