"""Daily-brief run ledger — one row per (brief_kind, business_date, prompt version).

Not append-only: a failed run may be re-driven to completed by a retry
on the same day / same prompt version. What we protect against is
double-billing the LLM for the same brief, not retracting the audit
trail of prior attempts.

See ``specs/daily_brief_cron.md`` §7.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, TimestampedMixin, uuid7


class BriefRun(TenantScopedMixin, TimestampedMixin, Base):
    """One brief execution — pending / completed / skipped / failed."""

    __tablename__ = "brief_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    brief_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Asia/Taipei calendar date the brief is *about*.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    retrieved_chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
        default=dict,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "brief_kind IN ("
            "'today_top5','engineering_gaps',"
            "'investor_qa_prep','weekly_review')",
            name="ck_brief_runs_kind",
        ),
        CheckConstraint(
            "status IN ("
            "'pending','completed','skipped_duplicate',"
            "'skipped_empty_kb','failed')",
            name="ck_brief_runs_status",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_message IS NOT NULL)",
            name="ck_brief_runs_failed_has_error",
        ),
        Index(
            "uq_brief_runs_idempotent",
            "tenant_id",
            "brief_kind",
            "business_date",
            "prompt_template_version",
            unique=True,
        ),
        Index(
            "ix_brief_runs_tenant_kind_time",
            "tenant_id",
            "brief_kind",
            "business_date",
        ),
    )

__all__ = ["BriefRun"]
