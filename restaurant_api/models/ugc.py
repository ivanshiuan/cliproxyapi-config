"""UGC (使用者生成內容) submissions — 打卡 / Google 評論 / IG 貼文 換獎.

The acquisition lever that opens a *store-外* door: a member posts a Google
review / IG check-in, shows it to staff, who approve it for a points reward.
Because "did they really post a 5-star review?" can't be verified automatically,
the flow is human-in-the-loop: members submit, staff approve/reject, and only an
approval grants points. The submission row is the audit trail of that decision.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    Money,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class UgcKind(enum.StrEnum):
    """What the member did off-platform to earn the reward."""

    GOOGLE_REVIEW = "google_review"
    IG_POST = "ig_post"
    CHECKIN = "checkin"


class UgcStatus(enum.StrEnum):
    """Review lifecycle. Only ``pending → approved`` grants points."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class UgcSubmission(TenantScopedMixin, TimestampedMixin, Base):
    """A member's claim that they posted a review / check-in, pending staff review."""

    __tablename__ = "ugc_submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[UgcKind] = mapped_column(
        SQLEnum(UgcKind, name="ugc_kind", native_enum=False, length=16),
        nullable=False,
    )
    # Link / screenshot URL the member supplies as proof (optional).
    proof_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[UgcStatus] = mapped_column(
        SQLEnum(UgcStatus, name="ugc_status", native_enum=False, length=16),
        nullable=False,
        default=UgcStatus.PENDING,
        # native_enum=False stores the enum *name* ("PENDING"); the
        # server_default must match the name, not the value ("pending"), or a
        # DB-default row would be invisible to ``status == PENDING`` queries.
        server_default=UgcStatus.PENDING.name,
    )
    # Points granted on approval (0 until approved).
    reward_points: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ugc_tenant_status", "tenant_id", "status"),
        Index("ix_ugc_customer_time", "customer_id", "created_at"),
    )


__all__ = ["UgcKind", "UgcStatus", "UgcSubmission"]
