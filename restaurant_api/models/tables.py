"""Dining tables + table sessions — the floor-plan side of the POS.

``dining_tables`` is the physical floor plan (per store). ``table_sessions``
is one seating: a party occupies a table from open to close. Orders attach
to a session (``orders.table_session_id``), so "table status" is always a
projection of its live session + that session's orders — we deliberately do
NOT store a mutable ``status`` on the table row itself, to avoid the classic
stale-table-state bug class.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class TableSessionStatus(enum.StrEnum):
    """Lifecycle of one seating.

    Transitions:
        open → closed      (bill settled, table freed)
        open → cancelled   (opened by mistake / party left before ordering)

    Kitchen progress (備餐中/出餐完成) is derived from the session's order
    lines' ``kitchen_status`` — it is not duplicated here.
    """

    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class DiningTable(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A physical table on the floor plan.

    ``qr_token`` backs the iCHEF-style 桌位 QR (permanent table card): the
    customer-facing ordering URL embeds this opaque token instead of the
    table id, so a leaked/photographed QR can be rotated without renaming
    the table.
    """

    __tablename__ = "dining_tables"

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
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 區域 (e.g. 一樓 / 露台 / 包廂) — free text, used for grouping in the UI.
    zone: Mapped[str | None] = mapped_column(Text, nullable=True)
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    qr_token: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)

    sessions: Mapped[list[TableSession]] = relationship(back_populates="table")

    __table_args__ = (
        # One live table name per store; soft-deleted rows free the name up
        # for re-creation.
        Index(
            "uq_dining_tables_store_name_live",
            "store_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class TableSession(TenantScopedMixin, TimestampedMixin, Base):
    """One seating: party sits down (open) → settles the bill (closed).

    No soft delete — a mistaken open is ``cancelled``, never removed, so the
    floor history stays auditable.
    """

    __tablename__ = "table_sessions"

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
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dining_tables.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[TableSessionStatus] = mapped_column(
        SQLEnum(
            TableSessionStatus,
            name="table_session_status",
            native_enum=False,
            length=16,
        ),
        nullable=False,
        default=TableSessionStatus.OPEN,
        # NB: SQLEnum(native_enum=False) stores the member NAME ("OPEN"),
        # not the value — server defaults and index predicates must match.
        server_default=TableSessionStatus.OPEN.name,
    )
    party_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # SET NULL: keep the seating record even if the employee is later removed.
    opened_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Link back to a reservation when the party booked ahead (iCHEF-style
    # 訂位→自動排桌). SET NULL so purging old reservations keeps seatings.
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reservations.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    table: Mapped[DiningTable] = relationship(back_populates="sessions")

    __table_args__ = (
        # At most ONE open session per table — the DB-level guard against
        # double-opening a table from two POS devices at once.
        Index(
            "uq_table_sessions_one_open_per_table",
            "table_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        # Floor-plan board scan: "all open sessions in this store".
        Index("ix_table_sessions_store_status", "store_id", "status"),
    )


__all__ = [
    "DiningTable",
    "TableSession",
    "TableSessionStatus",
]
