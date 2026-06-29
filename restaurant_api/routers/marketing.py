"""FastAPI router — ``/marketing`` (Hermes-Claude-Codex AI marketing system).

Endpoint map
------------
Hermes (memory):
  POST   /marketing/memories              create a brand memory
  GET    /marketing/memories              list memories (filterable)
  GET    /marketing/memories/{id}         get one
  PATCH  /marketing/memories/{id}         update / score

Claude (creative):
  POST   /marketing/content/generate      generate content via Claude
  GET    /marketing/content               list assets
  GET    /marketing/content/{id}          get one
  PATCH  /marketing/content/{id}          approve / archive / edit

  POST   /marketing/strategy              generate strategy brief

Codex (execution):
  POST   /marketing/ai-campaigns          create campaign
  GET    /marketing/ai-campaigns          list campaigns
  GET    /marketing/ai-campaigns/{id}     get one
  POST   /marketing/ai-campaigns/{id}/execute  execute (push to segment)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from ..api.auth import require_admin
from ..api.deps import DbSession, TenantId
from ..api.errors import NotFoundError
from ..models.marketing import AiCampaign, AiCampaignStatus, AssetPlatform, AssetStatus, MemoryType
from ..schemas.marketing import (
    AiCampaignCreate,
    AiCampaignResponse,
    ContentAssetResponse,
    ContentAssetUpdate,
    ContentGenerateRequest,
    MemoryCreate,
    MemoryResponse,
    MemoryUpdate,
    StrategyRequest,
)
from ..services import marketing_service

_ADMIN = [Depends(require_admin)]

_Q_LIMIT = Query(default=50, ge=1, le=200)
_Q_MEMORY_TYPE: MemoryType | None = Query(default=None)
_Q_TAGS: list[str] | None = Query(default=None)
_Q_ASSET_STATUS: AssetStatus | None = Query(default=None)
_Q_ASSET_TYPE: str | None = Query(default=None)
_Q_PLATFORM: AssetPlatform | None = Query(default=None)
_Q_CAMPAIGN_STATUS: AiCampaignStatus | None = Query(default=None)

router = APIRouter(prefix="/marketing", tags=["marketing"])


# ── Hermes: Memory ─────────────────────────────────────────────────────────


@router.post(
    "/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立品牌記憶 (Hermes 記憶層)",
    dependencies=_ADMIN,
)
async def create_memory(
    payload: MemoryCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> MemoryResponse:
    return await marketing_service.create_memory(session, tenant_id, payload)  # type: ignore[return-value]


@router.get(
    "/memories",
    response_model=list[MemoryResponse],
    summary="列出品牌記憶 (依 use_count 降冪)",
    dependencies=_ADMIN,
)
async def list_memories(
    session: DbSession,
    tenant_id: TenantId,
    memory_type: MemoryType | None = _Q_MEMORY_TYPE,
    tags: list[str] | None = _Q_TAGS,
    limit: int = _Q_LIMIT,
) -> list[MemoryResponse]:
    return await marketing_service.list_memories(  # type: ignore[return-value]
        session, tenant_id, memory_type=memory_type, tags=tags, limit=limit
    )


@router.get(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
    summary="取單一品牌記憶",
    dependencies=_ADMIN,
)
async def get_memory(
    memory_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> MemoryResponse:
    return await marketing_service.get_memory(session, tenant_id, memory_id)  # type: ignore[return-value]


@router.patch(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
    summary="更新品牌記憶 (可設 effectiveness_score)",
    dependencies=_ADMIN,
)
async def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> MemoryResponse:
    return await marketing_service.update_memory(session, tenant_id, memory_id, payload)  # type: ignore[return-value]


# ── Claude: Content generation ─────────────────────────────────────────────


@router.post(
    "/content/generate",
    response_model=ContentAssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="透過 Claude 生成行銷文案 (Claude 創意層)",
    dependencies=_ADMIN,
)
async def generate_content(
    payload: ContentGenerateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ContentAssetResponse:
    return await marketing_service.generate_content(session, tenant_id, payload)  # type: ignore[return-value]


@router.get(
    "/content",
    response_model=list[ContentAssetResponse],
    summary="列出行銷素材",
    dependencies=_ADMIN,
)
async def list_assets(
    session: DbSession,
    tenant_id: TenantId,
    asset_status: AssetStatus | None = _Q_ASSET_STATUS,
    asset_type: str | None = _Q_ASSET_TYPE,
    platform: AssetPlatform | None = _Q_PLATFORM,
    limit: int = _Q_LIMIT,
) -> list[ContentAssetResponse]:
    return await marketing_service.list_assets(  # type: ignore[return-value]
        session,
        tenant_id,
        status=asset_status,
        asset_type=asset_type,
        platform=platform,
        limit=limit,
    )


@router.get(
    "/content/{asset_id}",
    response_model=ContentAssetResponse,
    summary="取單一行銷素材",
    dependencies=_ADMIN,
)
async def get_asset(
    asset_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> ContentAssetResponse:
    return await marketing_service.get_asset(session, tenant_id, asset_id)  # type: ignore[return-value]


@router.patch(
    "/content/{asset_id}",
    response_model=ContentAssetResponse,
    summary="更新素材狀態 (draft→approved→published→archived) 或修改內容",
    dependencies=_ADMIN,
)
async def update_asset(
    asset_id: uuid.UUID,
    payload: ContentAssetUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> ContentAssetResponse:
    if payload.status is not None:
        new_status = payload.status
    else:
        current = await marketing_service.get_asset(session, tenant_id, asset_id)
        new_status = current.status
    return await marketing_service.update_asset_status(  # type: ignore[return-value]
        session, tenant_id, asset_id, status=new_status, content=payload.content
    )


# ── Claude: Strategy ───────────────────────────────────────────────────────


@router.post(
    "/strategy",
    response_model=ContentAssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="產生行銷策略簡報 (Claude 策略層)",
    dependencies=_ADMIN,
)
async def generate_strategy(
    payload: StrategyRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> ContentAssetResponse:
    return await marketing_service.generate_strategy(session, tenant_id, payload)  # type: ignore[return-value]


# ── Codex: AI Campaign execution ───────────────────────────────────────────


@router.post(
    "/ai-campaigns",
    response_model=AiCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立 AI 行銷活動 (Codex 執行層)",
    dependencies=_ADMIN,
)
async def create_ai_campaign(
    payload: AiCampaignCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> AiCampaignResponse:
    return await marketing_service.create_ai_campaign(session, tenant_id, payload)  # type: ignore[return-value]


@router.get(
    "/ai-campaigns",
    response_model=list[AiCampaignResponse],
    summary="列出 AI 行銷活動",
    dependencies=_ADMIN,
)
async def list_ai_campaigns(
    session: DbSession,
    tenant_id: TenantId,
    campaign_status: AiCampaignStatus | None = _Q_CAMPAIGN_STATUS,
    limit: int = _Q_LIMIT,
) -> list[AiCampaignResponse]:
    return await marketing_service.list_campaigns(  # type: ignore[return-value]
        session, tenant_id, status=campaign_status, limit=limit
    )


@router.get(
    "/ai-campaigns/{campaign_id}",
    response_model=AiCampaignResponse,
    summary="取單一 AI 行銷活動",
    dependencies=_ADMIN,
)
async def get_ai_campaign(
    campaign_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> AiCampaignResponse:
    row = await session.get(AiCampaign, campaign_id)
    if not row or row.tenant_id != tenant_id:
        raise NotFoundError(f"AiCampaign {campaign_id} not found")
    return row  # type: ignore[return-value]


@router.post(
    "/ai-campaigns/{campaign_id}/execute",
    response_model=AiCampaignResponse,
    summary="執行 AI 行銷活動 (推播至目標分眾)",
    dependencies=_ADMIN,
)
async def execute_ai_campaign(
    campaign_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> AiCampaignResponse:
    return await marketing_service.execute_campaign(session, tenant_id, campaign_id)  # type: ignore[return-value]


__all__ = ["router"]
