"""Cash drawer sessions — the open/close cycle of a physical till.

Every shift opens a cash drawer with a known opening_float (typically
TWD 5000 in coins/small bills for change). Through the shift, cash sales
add and refunds/payouts subtract. At close, the cashier counts the drawer
and we reconcile against the expected balance.

The variance (closing_actual - expected) is the single most-watched KPI
for cash integrity. Persistent negative variance from one cashier is the
classic sign of skimming.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Money, TenantScopedMixin, TimestampedMixin, uuid7


class CashDrawerSession(TenantScopedMixin, TimestampedMixin, Base):
    """One row per open/close cycle of a register."""

    __tablename__ = "cash_drawer_sessions"

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
    register_no: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Physical register identifier if the store has multiple drawers.",
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    opened_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    closed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # 開店備用金 — typically TWD 5000.
    opening_float: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    # Computed at close: opening_float + cash_sales - cash_refunds - payouts.
    expected_close: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # What the cashier actually counted.
    closing_actual: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # Generated: closing_actual - expected_close. Positive = surplus, negative
    # = shortage. Null while session is still open.
    variance: Mapped[Decimal | None] = mapped_column(
        Money,
        Computed("closing_actual - expected_close", persisted=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_cash_sessions_store_open", "store_id", "opened_at"),
        # Only one open session per (store, register) at a time. Enforced as
        # a partial unique index — closed sessions don't count.
        Index(
            "uq_cash_sessions_active",
            "store_id",
            "register_no",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )


__all__ = ["CashDrawerSession"]
