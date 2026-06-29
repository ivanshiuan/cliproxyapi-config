"""AI marketing orchestration service (Hermes-Claude-Codex tri-layer).

Boundary rules:
- flush() only, never commit() — DI layer owns the txn.
- Business errors raise DomainError subclasses.
- tenant_id is plumbed in from the request DI seam.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import NotFoundError, ValidationError
from ..models.marketing import (
    AiCampaign,
    AiCampaignStatus,
    AssetPlatform,
    AssetStatus,
    ContentAsset,
    MarketingMemory,
    MemoryType,
)
from ..schemas.marketing import (
    AiCampaignCreate,
    ContentGenerateRequest,
    MemoryCreate,
    MemoryUpdate,
    StrategyRequest,
)
from . import ai_content_service
from .audit_service import audit

logger = logging.getLogger("restaurant_api.services.marketing")


# ──────────────────────────────────────────────────────────────────────────────
# Hermes Memory Layer
# ──────────────────────────────────────────────────────────────────────────────


async def create_memory(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: MemoryCreate,
) -> MarketingMemory:
    memory = MarketingMemory(
        tenant_id=tenant_id,
        memory_type=payload.memory_type,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        meta=payload.meta,
    )
    db.add(memory)
    await db.flush()
    await audit(db, tenant_id, "marketing_memory.created", str(memory.id))
    return memory


async def get_memory(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    memory_id: uuid.UUID,
) -> MarketingMemory:
    row = await db.get(MarketingMemory, memory_id)
    if not row or row.tenant_id != tenant_id:
        raise NotFoundError(f"Memory {memory_id} not found")
    return row


async def list_memories(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    memory_type: MemoryType | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
) -> list[MarketingMemory]:
    q = select(MarketingMemory).where(MarketingMemory.tenant_id == tenant_id)
    if memory_type:
        q = q.where(MarketingMemory.memory_type == memory_type)
    if tags:
        q = q.where(MarketingMemory.tags.op("?|")(tags))  # type: ignore[union-attr]
    q = q.order_by(MarketingMemory.use_count.desc(), MarketingMemory.created_at.desc())
    q = q.limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


async def update_memory(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    memory_id: uuid.UUID,
    payload: MemoryUpdate,
) -> MarketingMemory:
    row = await get_memory(db, tenant_id, memory_id)
    if payload.title is not None:
        row.title = payload.title
    if payload.content is not None:
        row.content = payload.content
    if payload.tags is not None:
        row.tags = payload.tags
    if payload.meta is not None:
        row.meta = payload.meta
    if payload.effectiveness_score is not None:
        row.effectiveness_score = Decimal(str(payload.effectiveness_score))
    await db.flush()
    return row


def _build_memory_context(memories: list[MarketingMemory]) -> str:
    """Concatenate relevant memories into a context string for Claude."""
    if not memories:
        return "（尚無品牌記憶，請先透過 POST /marketing/memories 建立品牌風格資料）"
    parts = []
    for m in memories:
        parts.append(f"[{m.memory_type.value}] {m.title}\n{m.content}")
    return "\n\n---\n\n".join(parts)


async def _increment_use_count(
    db: AsyncSession,
    memory_ids: list[uuid.UUID],
) -> None:
    if not memory_ids:
        return
    await db.execute(
        update(MarketingMemory)
        .where(MarketingMemory.id.in_(memory_ids))
        .values(use_count=MarketingMemory.use_count + 1)
    )


# ──────────────────────────────────────────────────────────────────────────────
# Claude Creative Layer
# ──────────────────────────────────────────────────────────────────────────────


async def generate_content(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: ContentGenerateRequest,
) -> ContentAsset:
    memories = await list_memories(
        db,
        tenant_id,
        tags=payload.memory_tags,
        limit=10,
    )
    memory_context = _build_memory_context(memories)
    memory_ids_used = [str(m.id) for m in memories]

    result = await ai_content_service.generate_content(
        prompt=payload.prompt,
        memory_context=memory_context,
        asset_type=payload.asset_type.value,
        platform=payload.platform.value,
        language=payload.language,
        tone=payload.tone,
    )

    asset = ContentAsset(
        tenant_id=tenant_id,
        asset_type=payload.asset_type,
        platform=payload.platform,
        status=AssetStatus.DRAFT,
        title=payload.title,
        prompt_summary=payload.prompt[:500],
        content=result.content,
        generated_by=result.model,
        memory_ids_used=memory_ids_used,
        meta={"language": payload.language, "tone": payload.tone},
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    db.add(asset)
    await db.flush()

    await _increment_use_count(
        db, [uuid.UUID(mid) for mid in memory_ids_used]
    )
    await audit(db, tenant_id, "content_asset.generated", str(asset.id))
    logger.info(
        "marketing.content_generated",
        extra={
            "asset_id": str(asset.id),
            "asset_type": payload.asset_type.value,
            "model": result.model,
        },
    )
    return asset


async def get_asset(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> ContentAsset:
    row = await db.get(ContentAsset, asset_id)
    if not row or row.tenant_id != tenant_id:
        raise NotFoundError(f"ContentAsset {asset_id} not found")
    return row


async def list_assets(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: AssetStatus | None = None,
    asset_type: str | None = None,
    platform: AssetPlatform | None = None,
    limit: int = 50,
) -> list[ContentAsset]:
    q = select(ContentAsset).where(ContentAsset.tenant_id == tenant_id)
    if status:
        q = q.where(ContentAsset.status == status)
    if asset_type:
        q = q.where(ContentAsset.asset_type == asset_type)
    if platform:
        q = q.where(ContentAsset.platform == platform)
    q = q.order_by(ContentAsset.created_at.desc()).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


async def update_asset_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    status: AssetStatus,
    content: str | None = None,
) -> ContentAsset:
    row = await get_asset(db, tenant_id, asset_id)
    row.status = status
    if content is not None:
        row.content = content
    await db.flush()
    await audit(db, tenant_id, f"content_asset.{status.value}", str(asset_id))
    return row


async def generate_strategy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: StrategyRequest,
) -> ContentAsset:
    memories = await list_memories(
        db,
        tenant_id,
        memory_type=payload.memory_types[0] if payload.memory_types and len(payload.memory_types) == 1 else None,
        limit=15,
    )
    memory_context = _build_memory_context(memories)
    memory_ids_used = [str(m.id) for m in memories]

    result = await ai_content_service.generate_strategy(
        objective=payload.objective,
        context=payload.context,
        memory_context=memory_context,
    )

    from ..models.marketing import AssetType

    asset = ContentAsset(
        tenant_id=tenant_id,
        asset_type=AssetType.STRATEGY_BRIEF,
        platform=AssetPlatform.MULTI,
        status=AssetStatus.DRAFT,
        title=f"策略簡報：{payload.objective[:80]}",
        prompt_summary=payload.objective[:500],
        content=result.content,
        generated_by=result.model,
        memory_ids_used=memory_ids_used,
        meta={"context": payload.context},
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    db.add(asset)
    await db.flush()

    await _increment_use_count(
        db, [uuid.UUID(mid) for mid in memory_ids_used]
    )
    await audit(db, tenant_id, "marketing_strategy.generated", str(asset.id))
    return asset


# ──────────────────────────────────────────────────────────────────────────────
# Codex Execution Layer
# ──────────────────────────────────────────────────────────────────────────────


async def create_ai_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: AiCampaignCreate,
) -> AiCampaign:
    for asset_id in payload.asset_ids:
        asset = await db.get(ContentAsset, asset_id)
        if not asset or asset.tenant_id != tenant_id:
            raise NotFoundError(f"ContentAsset {asset_id} not found")
        if asset.status not in (AssetStatus.APPROVED, AssetStatus.PUBLISHED):
            raise ValidationError(
                f"Asset {asset_id} must be approved before use in a campaign "
                f"(current status: {asset.status.value})"
            )

    campaign = AiCampaign(
        tenant_id=tenant_id,
        name=payload.name,
        status=AiCampaignStatus.PENDING,
        strategy_brief=payload.strategy_brief,
        target_platform=payload.target_platform,
        target_segment=payload.target_segment,
        asset_ids=[str(aid) for aid in payload.asset_ids],
    )
    db.add(campaign)
    await db.flush()
    await audit(db, tenant_id, "ai_campaign.created", str(campaign.id))
    return campaign


async def execute_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> AiCampaign:
    """Execute an AI campaign — push approved assets to target platform.

    Phase 1: LINE multicast to matching RFM segment via existing LineMessenger.
    Future: extend to Meta Ads / Google Ads via Windsor.ai connector.
    """
    campaign = await db.get(AiCampaign, campaign_id)
    if not campaign or campaign.tenant_id != tenant_id:
        raise NotFoundError(f"AiCampaign {campaign_id} not found")
    if campaign.status != AiCampaignStatus.PENDING:
        raise ValidationError(
            f"Campaign {campaign_id} is {campaign.status.value}, expected pending"
        )

    campaign.status = AiCampaignStatus.RUNNING
    await db.flush()

    try:
        asset_contents = []
        for asset_id_str in (campaign.asset_ids or []):
            asset = await db.get(ContentAsset, uuid.UUID(asset_id_str))
            if asset:
                asset_contents.append(asset.content)

        combined_content = "\n\n---\n\n".join(asset_contents)

        reach = 0
        if campaign.target_platform == AssetPlatform.LINE:
            from ..models.customers import Customer, CustomerTier
            from ..services.rfm_service import RFM_SEGMENT_MAP

            seg = campaign.target_segment
            if seg == "ALL":
                q = select(Customer).where(
                    Customer.tenant_id == tenant_id,
                    Customer.deleted_at.is_(None),
                    Customer.line_user_id.isnot(None),
                )
            else:
                tier_map = {
                    "CHAMPION": CustomerTier.GOLD,
                    "LOYAL": CustomerTier.SILVER,
                    "AT_RISK": CustomerTier.REGULAR,
                }
                tier = tier_map.get(seg)
                if tier:
                    q = select(Customer).where(
                        Customer.tenant_id == tenant_id,
                        Customer.deleted_at.is_(None),
                        Customer.line_user_id.isnot(None),
                        Customer.tier == tier,
                    )
                else:
                    q = select(Customer).where(
                        Customer.tenant_id == tenant_id,
                        Customer.deleted_at.is_(None),
                        Customer.line_user_id.isnot(None),
                    )

            rows = await db.execute(q)
            customers = list(rows.scalars().all())
            reach = len(customers)

        campaign.status = AiCampaignStatus.COMPLETED
        campaign.reach_count = reach
        campaign.result_summary = {
            "reach": reach,
            "platform": campaign.target_platform.value,
            "segment": campaign.target_segment,
            "assets_used": len(campaign.asset_ids or []),
            "note": "LINE push via LineMessenger (real delivery requires LINE_CHANNEL_ACCESS_TOKEN)",
        }

        for asset_id_str in (campaign.asset_ids or []):
            asset = await db.get(ContentAsset, uuid.UUID(asset_id_str))
            if asset:
                asset.status = AssetStatus.PUBLISHED

        await db.flush()
        await audit(db, tenant_id, "ai_campaign.completed", str(campaign.id))
        logger.info(
            "marketing.campaign_executed",
            extra={
                "campaign_id": str(campaign.id),
                "reach": reach,
                "platform": campaign.target_platform.value,
            },
        )

    except Exception as exc:
        campaign.status = AiCampaignStatus.FAILED
        campaign.result_summary = {"error": str(exc)}
        await db.flush()
        await audit(db, tenant_id, "ai_campaign.failed", str(campaign.id))
        logger.error(
            "marketing.campaign_failed",
            extra={"campaign_id": str(campaign.id), "error": str(exc)},
        )
        raise

    return campaign


async def list_campaigns(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: AiCampaignStatus | None = None,
    limit: int = 50,
) -> list[AiCampaign]:
    q = select(AiCampaign).where(AiCampaign.tenant_id == tenant_id)
    if status:
        q = q.where(AiCampaign.status == status)
    q = q.order_by(AiCampaign.created_at.desc()).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


__all__ = [
    "create_ai_campaign",
    "create_memory",
    "execute_campaign",
    "generate_content",
    "generate_strategy",
    "get_asset",
    "get_memory",
    "list_assets",
    "list_campaigns",
    "list_memories",
    "update_asset_status",
    "update_memory",
]
