"""HR — 排班 (shifts) / 打卡 (time_clocks) / 請假 (leave_requests)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, TimestampedMixin, uuid7


class LeaveType(enum.StrEnum):
    """Closed set of leave types under Taiwanese labour law (勞基法)."""

    ANNUAL = "特休"
    PERSONAL = "事假"
    SICK = "病假"
    WEDDING = "婚假"
    BEREAVEMENT = "喪假"
    MATERNITY = "產假"
    MENSTRUAL = "生理假"
    OFFICIAL = "公假"
    OTHER = "其他"


class LeaveStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class Shift(TenantScopedMixin, TimestampedMixin, Base):
    """A scheduled (planned) shift — ground truth comes from ``TimeClock``."""

    __tablename__ = "shifts"

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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    scheduled_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    role_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_shifts_employee_start", "employee_id", "scheduled_start"),
        Index("ix_shifts_store_start", "store_id", "scheduled_start"),
    )


class TimeClock(TenantScopedMixin, TimestampedMixin, Base):
    """Actual clock-in / clock-out.

    Hours are pre-bucketed by 勞基法 tier so the P&L view doesn't have to
    re-derive overtime classification at query time:

    - ``regular_hours``: 正常工時 (1.0x)
    - ``overtime_tier1_hours``: 加班前2小時 (1.34x)
    - ``overtime_tier2_hours``: 加班後2小時 (1.67x)
    - ``holiday_hours``: 假日加班 (2.0x)
    """

    __tablename__ = "time_clocks"

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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    clock_in: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    clock_out: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    regular_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    overtime_tier1_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    overtime_tier2_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    holiday_hours: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_time_clocks_employee_in", "employee_id", "clock_in"),
        Index("ix_time_clocks_store_in", "store_id", "clock_in"),
    )


class LeaveRequest(TenantScopedMixin, TimestampedMixin, Base):
    """請假 — leave application with approval workflow."""

    __tablename__ = "leave_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    leave_type: Mapped[LeaveType] = mapped_column(
        SQLEnum(LeaveType, name="leave_type", native_enum=False, length=16),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    status: Mapped[LeaveStatus] = mapped_column(
        SQLEnum(LeaveStatus, name="leave_status", native_enum=False, length=16),
        nullable=False,
        default=LeaveStatus.PENDING,
        server_default=LeaveStatus.PENDING.value,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SET NULL: don't void the leave record if the approver later leaves.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_leave_requests_employee_start", "employee_id", "start_at"),
    )


__all__ = [
    "LeaveRequest",
    "LeaveStatus",
    "LeaveType",
    "Shift",
    "TimeClock",
]
