"""Audit log — every privileged event writes a row here.

Why a generic single-table audit instead of per-table history tables:
- Single query surface for compliance audits ("show me every comp by
  Alice last month").
- Easier to RLS-scope by tenant.
- Append-only by design — paired with a DB rule in the migration so no
  one (including the swarm) can rewrite history.

Use cases the commander expects to read:
- 招待 / 折扣 / 折讓 events with actor + reason
- Cash drawer variance closures
- Manual stock adjustments
- Employee role changes / termination
- Order voids (the paper trail an auditor will demand)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, uuid7


class AuditLog(TenantScopedMixin, Base):
    """One row per privileged business event.

    Immutability: enforced by a DB-level rule in the migration. No ORM
    UPDATE path is exposed; if you need to retract an event, write a new
    `compensating` action that references the original via ``target_id``.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # store_id nullable — tenant-level events (role changes, settings) have none.
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # SET NULL on actor delete — soft-deleted employees still leave a trail.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Dotted action namespace: "discount.applied", "comp.granted",
    # "stock.adjusted", "order.voided", "cash_drawer.closed", "employee.role_changed"
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Target row pointer — denormalised on purpose so the log is self-contained
    # even if the target table later gets renamed / split.
    target_table: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    # Diff snapshot. ``before`` may be null for create-only events;
    # ``after`` may be null for delete events.
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Optional context fields for forensic reconstruction.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_audit_log_action_time", "action", "occurred_at"),
        Index("ix_audit_log_target", "target_table", "target_id"),
        Index("ix_audit_log_store_time", "store_id", "occurred_at"),
    )


__all__ = ["AuditLog"]
