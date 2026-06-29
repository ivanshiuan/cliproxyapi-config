"""Pydantic v2 schemas for the /marketing router (Hermes-Claude-Codex AI system)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.marketing import (
    AiCampaignStatus,
    AssetPlatform,
    AssetStatus,
    AssetType,
    MemoryType,
)


# ──────────────────────────────────────────────────────────────────────────────
# Hermes Memory Layer
# ──────────────────────────────────────────────────────────────────────────────


class MemoryCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_type: MemoryType
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None
    effectiveness_score: float | None = Field(default=None, ge=0, le=100)


class MemoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    memory_type: MemoryType
    title: str
    content: str
    tags: list[str] | None
    meta: dict[str, Any] | None
    use_count: int
    effectiveness_score: float | None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Claude Creative Layer
# ──────────────────────────────────────────────────────────────────────────────


class ContentGenerateRequest(BaseModel):
    """Request to generate marketing content via Claude."""

    model_config = ConfigDict(frozen=True)

    asset_type: AssetType
    platform: AssetPlatform
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(
        min_length=10,
        max_length=2000,
        description="What marketing content to generate",
    )
    memory_tags: list[str] | None = Field(
        default=None,
        description="Filter memories by these tags when building context",
    )
    language: str = Field(default="zh-TW", description="Output language")
    tone: str | None = Field(
        default=None,
        max_length=100,
        description="Desired tone (e.g. 活潑、正式、親切)",
    )


class ContentAssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    asset_type: AssetType
    platform: AssetPlatform
    status: AssetStatus
    title: str
    prompt_summary: str
    content: str
    generated_by: str
    memory_ids_used: list[str] | None
    meta: dict[str, Any] | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
    updated_at: datetime


class ContentAssetUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AssetStatus | None = None
    content: str | None = Field(default=None, min_length=1)


# ──────────────────────────────────────────────────────────────────────────────
# Codex Execution Layer
# ──────────────────────────────────────────────────────────────────────────────


class AiCampaignCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=200)
    target_platform: AssetPlatform
    target_segment: str = Field(
        default="ALL",
        max_length=50,
        description="RFM segment name (CHAMPION/AT_RISK/ALL etc.)",
    )
    asset_ids: list[UUID] = Field(
        min_length=1,
        description="ContentAsset IDs to distribute",
    )
    strategy_brief: str | None = Field(default=None, max_length=5000)


class AiCampaignResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    name: str
    status: AiCampaignStatus
    strategy_brief: str | None
    target_platform: AssetPlatform
    target_segment: str
    asset_ids: list[str] | None
    reach_count: int | None
    result_summary: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────────
# Strategy analysis
# ──────────────────────────────────────────────────────────────────────────────


class StrategyRequest(BaseModel):
    """Ask Claude to produce a marketing strategy brief for this tenant."""

    model_config = ConfigDict(frozen=True)

    objective: str = Field(
        min_length=10,
        max_length=1000,
        description="Marketing objective (e.g. 提升回訪率、推廣新品)",
    )
    context: str | None = Field(
        default=None,
        max_length=2000,
        description="Extra context (season, budget, constraints)",
    )
    memory_types: list[MemoryType] | None = Field(
        default=None,
        description="Which memory types to pull for context",
    )


class StrategyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: UUID
    strategy: str
    recommended_assets: list[str]
    input_tokens: int | None
    output_tokens: int | None


__all__ = [
    "AiCampaignCreate",
    "AiCampaignResponse",
    "ContentAssetResponse",
    "ContentAssetUpdate",
    "ContentGenerateRequest",
    "MemoryCreate",
    "MemoryResponse",
    "MemoryUpdate",
    "StrategyRequest",
    "StrategyResponse",
]
