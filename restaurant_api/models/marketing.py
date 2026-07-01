"""ORM models for the AI marketing system (Hermes-Claude-Codex tri-layer).

Three tables map to the three-layer architecture:
  - MarketingMemory  → Hermes skill/memory store
  - ContentAsset     → Claude-generated creative assets
  - AiCampaign       → Codex-executed distribution campaigns
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Enum,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, TimestampedMixin, uuid7

# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class MemoryType(enum.StrEnum):
    BRAND_VOICE = "brand_voice"
    AUDIENCE_SEGMENT = "audience_segment"
    SUCCESSFUL_SKILL = "successful_skill"
    FAILED_ATTEMPT = "failed_attempt"
    COMPETITIVE_INSIGHT = "competitive_insight"
    CAMPAIGN_LEARNING = "campaign_learning"


class AssetType(enum.StrEnum):
    SOCIAL_POST = "social_post"
    LINE_MESSAGE = "line_message"
    PROMOTION_COPY = "promotion_copy"
    AD_CREATIVE = "ad_creative"
    EMAIL = "email"
    STRATEGY_BRIEF = "strategy_brief"


class AssetPlatform(enum.StrEnum):
    LINE = "line"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    GOOGLE_ADS = "google_ads"
    WECHAT = "wechat"
    MULTI = "multi"


class AssetStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AiCampaignStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ──────────────────────────────────────────────────────────────────────────────
# Hermes Memory Layer
# ──────────────────────────────────────────────────────────────────────────────


class MarketingMemory(Base, TenantScopedMixin, TimestampedMixin):
    """Persistent knowledge store — brand voice, audience insights, learned skills.

    Mirrors the Hermes "self-evolving skill memory" concept: every successful
    campaign outcome gets distilled into a reusable memory entry that future
    Claude/Codex invocations can pull from.
    """

    __tablename__ = "marketing_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    memory_type: Mapped[MemoryType] = mapped_column(
        Enum(MemoryType, name="marketing_memory_type", native_enum=False, length=32),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    effectiveness_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )


# ──────────────────────────────────────────────────────────────────────────────
# Claude Creative Layer
# ──────────────────────────────────────────────────────────────────────────────


class ContentAsset(Base, TenantScopedMixin, TimestampedMixin):
    """AI-generated content pieces from the Claude strategy/creative layer.

    Each asset tracks which memory IDs were used as context during generation,
    enabling the Hermes layer to close the loop (score → update memory).
    """

    __tablename__ = "content_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="content_asset_type", native_enum=False, length=32),
        nullable=False,
        index=True,
    )
    platform: Mapped[AssetPlatform] = mapped_column(
        Enum(AssetPlatform, name="content_asset_platform", native_enum=False, length=32),
        nullable=False,
    )
    status: Mapped[AssetStatus] = mapped_column(
        Enum(AssetStatus, name="content_asset_status", native_enum=False, length=16),
        nullable=False,
        default=AssetStatus.DRAFT,
        server_default="draft",
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    memory_ids_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ──────────────────────────────────────────────────────────────────────────────
# Codex Execution Layer
# ──────────────────────────────────────────────────────────────────────────────


class AiCampaign(Base, TenantScopedMixin, TimestampedMixin):
    """Automated campaign execution records from the Codex distribution layer.

    Ties a set of ContentAssets to a target audience segment and platform,
    and records the execution result for Hermes to learn from.
    """

    __tablename__ = "ai_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[AiCampaignStatus] = mapped_column(
        Enum(AiCampaignStatus, name="ai_campaign_status", native_enum=False, length=16),
        nullable=False,
        default=AiCampaignStatus.PENDING,
        server_default="pending",
        index=True,
    )
    strategy_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_platform: Mapped[AssetPlatform] = mapped_column(
        Enum(AssetPlatform, name="ai_campaign_platform", native_enum=False, length=32),
        nullable=False,
    )
    target_segment: Mapped[str] = mapped_column(
        String(50), nullable=False, default="ALL", server_default="ALL"
    )
    asset_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reach_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


__all__ = [
    "AiCampaign",
    "AiCampaignStatus",
    "AssetPlatform",
    "AssetStatus",
    "AssetType",
    "ContentAsset",
    "MarketingMemory",
    "MemoryType",
]
