"""brief_runs table (BUFF OS Week 3 — daily brief ledger)

Adds the ``brief_runs`` table used by ``jobs.daily_brief`` to record
every scheduled brief execution:

* One row per (tenant, brief_kind, business_date, prompt_template_version)
  — enforced by a UNIQUE index. Bumping the prompt version deliberately
  yields a new row so the audit trail survives prompt evolution.
* NOT append-only: a failed run may be UPDATE'd to completed on retry.

See ``specs/daily_brief_cron.md`` §7.

Revision ID: 6d4a1e2b8f37
Revises: 8b3f2a7c9e14
Create Date: 2026-06-27 13:00:00.000000+08:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6d4a1e2b8f37"
down_revision: str | Sequence[str] | None = "8b3f2a7c9e14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brief_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brief_kind", sa.String(length=32), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "prompt_template_version", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "retrieved_chunk_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "prompt_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "latency_ms", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("output_markdown", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "brief_kind IN ("
            "'today_top5','engineering_gaps',"
            "'investor_qa_prep','weekly_review')",
            name="ck_brief_runs_kind",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending','completed','skipped_duplicate',"
            "'skipped_empty_kb','failed')",
            name="ck_brief_runs_status",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error_message IS NOT NULL)",
            name="ck_brief_runs_failed_has_error",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brief_runs_tenant_id"),
        "brief_runs",
        ["tenant_id"],
        unique=False,
    )
    # Idempotency: same prompt version + same date can only run once.
    op.create_index(
        "uq_brief_runs_idempotent",
        "brief_runs",
        [
            "tenant_id",
            "brief_kind",
            "business_date",
            "prompt_template_version",
        ],
        unique=True,
    )
    op.create_index(
        "ix_brief_runs_tenant_kind_time",
        "brief_runs",
        ["tenant_id", "brief_kind", "business_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_brief_runs_tenant_kind_time", table_name="brief_runs")
    op.drop_index("uq_brief_runs_idempotent", table_name="brief_runs")
    op.drop_index(op.f("ix_brief_runs_tenant_id"), table_name="brief_runs")
    op.drop_table("brief_runs")
