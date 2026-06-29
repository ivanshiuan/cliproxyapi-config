"""AI marketing system — Hermes-Claude-Codex tri-layer tables

Revision ID: c3d4e5f6a1b2
Revises: 41772db92af3
Create Date: 2026-06-29 00:00:00.000000+08:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a1b2"
down_revision: str | Sequence[str] | None = "41772db92af3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Hermes: marketing_memories ─────────────────────────────────────────
    op.create_table(
        "marketing_memories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "memory_type",
            sa.Enum(
                "brand_voice",
                "audience_segment",
                "successful_skill",
                "failed_attempt",
                "competitive_insight",
                "campaign_learning",
                name="marketing_memory_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effectiveness_score", sa.Numeric(precision=5, scale=2), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_marketing_memories_tenant_id"), "marketing_memories", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_marketing_memories_memory_type"), "marketing_memories", ["memory_type"]
    )

    # ── Claude: content_assets ─────────────────────────────────────────────
    op.create_table(
        "content_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "asset_type",
            sa.Enum(
                "social_post",
                "line_message",
                "promotion_copy",
                "ad_creative",
                "email",
                "strategy_brief",
                name="content_asset_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "platform",
            sa.Enum(
                "line",
                "instagram",
                "facebook",
                "google_ads",
                "wechat",
                "multi",
                name="content_asset_platform",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "approved",
                "published",
                "archived",
                name="content_asset_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("prompt_summary", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.String(100), nullable=False),
        sa.Column("memory_ids_used", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_content_assets_tenant_id"), "content_assets", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_content_assets_asset_type"), "content_assets", ["asset_type"]
    )
    op.create_index(op.f("ix_content_assets_status"), "content_assets", ["status"])

    # ── Codex: ai_campaigns ────────────────────────────────────────────────
    op.create_table(
        "ai_campaigns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                "cancelled",
                name="ai_campaign_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("strategy_brief", sa.Text(), nullable=True),
        sa.Column(
            "target_platform",
            sa.Enum(
                "line",
                "instagram",
                "facebook",
                "google_ads",
                "wechat",
                "multi",
                name="ai_campaign_platform",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_segment", sa.String(50), nullable=False, server_default="ALL"),
        sa.Column("asset_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reach_count", sa.Integer(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_campaigns_tenant_id"), "ai_campaigns", ["tenant_id"])
    op.create_index(op.f("ix_ai_campaigns_status"), "ai_campaigns", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_campaigns_status"), table_name="ai_campaigns")
    op.drop_index(op.f("ix_ai_campaigns_tenant_id"), table_name="ai_campaigns")
    op.drop_table("ai_campaigns")

    op.drop_index(op.f("ix_content_assets_status"), table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_asset_type"), table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_tenant_id"), table_name="content_assets")
    op.drop_table("content_assets")

    op.drop_index(
        op.f("ix_marketing_memories_memory_type"), table_name="marketing_memories"
    )
    op.drop_index(
        op.f("ix_marketing_memories_tenant_id"), table_name="marketing_memories"
    )
    op.drop_table("marketing_memories")
