"""CRM relationship layer -- notes, tasks, tags, interactions (Twenty CRM benchmark).

Upgrades the loyalty-only member model into a relationship-management CRM:

- ``CustomerNote``        many authored, timestamped notes (Twenty Notes)
- ``CustomerTask``        follow-up tasks with assignee / due date / status (Twenty Tasks)
- ``CustomerTag`` + link  tenant-defined labels, beyond the fixed ``CustomerTier`` enum
- ``CustomerInteraction`` logged touchpoints (LINE / phone / in person) feeding the timeline

The unified customer **timeline** is not a table: ``services/crm_timeline_service``
merges orders / points / stored value / referrals plus this module's notes,
tasks and interactions into one chronological feed. See docs/14.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)

# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


class CustomerNote(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A free-form, authored note attached to a customer (Twenty Notes).

    Unlike ``Customer.notes`` (a single overwrite-able text field) these are
    many, timestamped, and attributed to the staff member who wrote them.
    """

    __tablename__ = "customer_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL: keep the note even if the authoring employee is removed.
    author_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_customer_notes_customer_time", "customer_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class CustomerTaskStatus(enum.StrEnum):
    """Follow-up task lifecycle."""

    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class CustomerTask(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A staff follow-up task tied to a customer (Twenty Tasks).

    e.g. 「打給 VIP 王先生關心上次客訴」、「生日禮待取」。Drives relationship
    management: assignee, due date, and an open→done/cancelled lifecycle.
    """

    __tablename__ = "customer_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[CustomerTaskStatus] = mapped_column(
        SQLEnum(
            CustomerTaskStatus,
            name="customer_task_status",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CustomerTaskStatus.OPEN,
        server_default=CustomerTaskStatus.OPEN.value,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_customer_tasks_customer_time", "customer_id", "created_at"),
        Index("ix_customer_tasks_assignee_status", "assignee_employee_id", "status"),
        Index("ix_customer_tasks_status_due", "status", "due_at"),
    )


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class CustomerTag(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A reusable, free-form label (VIP / 素食 / 過敏:花生 / 生日月).

    Complements the fixed ``CustomerTier`` enum — tenants coin their own.
    """

    __tablename__ = "customer_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # Optional UI colour hint, e.g. "#E53E3E". No validation beyond length.
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        Index(
            "uq_customer_tags_tenant_name_live",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class CustomerTagLink(TenantScopedMixin, Base):
    """M2M link attaching a tag to a customer."""

    __tablename__ = "customer_tag_links"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_tags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_customer_tag_links_pair",
            "customer_id",
            "tag_id",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------


class InteractionChannel(enum.StrEnum):
    """How a touchpoint happened."""

    LINE = "line"
    PHONE = "phone"
    IN_PERSON = "in_person"
    EMAIL = "email"
    OTHER = "other"


class InteractionDirection(enum.StrEnum):
    """Who initiated the touchpoint."""

    INBOUND = "inbound"  # customer reached out
    OUTBOUND = "outbound"  # staff reached out


class CustomerInteraction(TenantScopedMixin, Base):
    """A logged touchpoint with a customer (Twenty activities).

    Append-style record (no soft delete) feeding the customer timeline:
    「11/20 LINE 詢問訂位」、「11/22 電話回訪客訴」。
    """

    __tablename__ = "customer_interactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[InteractionChannel] = mapped_column(
        SQLEnum(
            InteractionChannel,
            name="interaction_channel",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    direction: Mapped[InteractionDirection] = mapped_column(
        SQLEnum(
            InteractionDirection,
            name="interaction_direction",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_customer_interactions_customer_time",
            "customer_id",
            "occurred_at",
        ),
    )


__all__ = [
    "CustomerInteraction",
    "CustomerNote",
    "CustomerTag",
    "CustomerTagLink",
    "CustomerTask",
    "CustomerTaskStatus",
    "InteractionChannel",
    "InteractionDirection",
]
