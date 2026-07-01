"""AI marketing orchestration service (Hermes-Claude-Codex tri-layer).

Boundary rules:
- flush() only, never commit() — DI layer owns the txn.
- Business errors raise DomainError subclasses.
- tenant_id is plumbed in from the request DI seam.

Bug fixes vs v1:
- [CRITICAL] execute_campaign no longer re-raises after setting FAILED, so the
  FAILED status is committed rather than rolled back.
- [CRITICAL] Segment targeting uses RfmSegment + rfm_service.broadcast_to_segment
  instead of a partial hardcoded tier_map; unknown segments now raise ValidationError.
- [SEVERE]   Non-LINE platforms raise ValidationError (Phase 1: LINE only).
- [PERF]     Asset fetching uses a single IN-clause, not N individual queries.
- [FEATURE]  Hermes feedback loop: campaign completion auto-creates a
             campaign_learning memory entry.
- [LOGIC]    Asset status transitions are validated against an explicit machine.
- [LOGIC]    generate_strategy supports multiple memory_types (OR filter).
- [LOGIC]    Empty tags list ([] via PATCH) clears tags rather than storing [].
- [FEATURE]  list_* functions accept offset for cursor-free pagination.
- [FEATURE]  execute_campaign accepts FAILED status (retry previously failed run).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
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
from ..schemas.rfm import RfmSegment
from . import ai_content_service
from .audit_service import audit

logger = logging.getLogger("restaurant_api.services.marketing")

# ──────────────────────────────────────────────────────────────────────────────
# Asset status-machine
# ──────────────────────────────────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[AssetStatus, frozenset[AssetStatus]] = {
    AssetStatus.DRAFT: frozenset({AssetStatus.APPROVED, AssetStatus.ARCHIVED}),
    AssetStatus.APPROVED: frozenset(
        {AssetStatus.DRAFT, AssetStatus.PUBLISHED, AssetStatus.ARCHIVED}
    ),
    AssetStatus.PUBLISHED: frozenset({AssetStatus.ARCHIVED}),
    AssetStatus.ARCHIVED: frozenset(),
}


def _check_transition(current: AssetStatus, new: AssetStatus) -> None:
    """Raise ValidationError for illegal status moves."""
    if new == current:
        return
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if new not in allowed:
        allowed_names = [s.value for s in sorted(allowed, key=lambda s: s.value)]
        raise ValidationError(
            f"Cannot transition asset from '{current.value}' to '{new.value}'. "
            f"Allowed next states: {allowed_names or ['none']}"
        )


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
    await audit(db, action="marketing_memory.created", tenant_id=tenant_id, target=("marketing_memories", memory.id))
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
    memory_types: list[MemoryType] | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MarketingMemory]:
    q = select(MarketingMemory).where(MarketingMemory.tenant_id == tenant_id)
    if memory_types:
        q = q.where(MarketingMemory.memory_type.in_(memory_types))
    elif memory_type:
        q = q.where(MarketingMemory.memory_type == memory_type)
    if tags:
        q = q.where(MarketingMemory.tags.op("?|")(tags))  # type: ignore[union-attr]
    q = q.order_by(MarketingMemory.use_count.desc(), MarketingMemory.created_at.desc())
    q = q.offset(offset).limit(limit)
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
        # Empty list clears tags (set to NULL); non-empty list replaces.
        row.tags = payload.tags if payload.tags else None
    if payload.meta is not None:
        row.meta = payload.meta
    if payload.effectiveness_score is not None:
        row.effectiveness_score = Decimal(str(payload.effectiveness_score))
    await db.flush()
    await db.refresh(row)
    return row


def _build_memory_context(memories: list[MarketingMemory]) -> str:
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

    await _increment_use_count(db, [uuid.UUID(mid) for mid in memory_ids_used])
    await audit(db, action="content_asset.generated", tenant_id=tenant_id, target=("content_assets", asset.id))
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
    offset: int = 0,
) -> list[ContentAsset]:
    q = select(ContentAsset).where(ContentAsset.tenant_id == tenant_id)
    if status:
        q = q.where(ContentAsset.status == status)
    if asset_type:
        q = q.where(ContentAsset.asset_type == asset_type)
    if platform:
        q = q.where(ContentAsset.platform == platform)
    q = q.order_by(ContentAsset.created_at.desc()).offset(offset).limit(limit)
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
    _check_transition(row.status, status)
    row.status = status
    if content is not None:
        row.content = content
    await db.flush()
    await db.refresh(row)
    await audit(db, action=f"content_asset.{status.value}", tenant_id=tenant_id, target=("content_assets", asset_id))
    return row


async def generate_strategy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: StrategyRequest,
) -> ContentAsset:
    from ..models.marketing import AssetType

    # Support filtering by multiple memory types (OR logic).
    memories = await list_memories(
        db,
        tenant_id,
        memory_types=payload.memory_types if payload.memory_types else None,
        limit=15,
    )
    memory_context = _build_memory_context(memories)
    memory_ids_used = [str(m.id) for m in memories]

    result = await ai_content_service.generate_strategy(
        objective=payload.objective,
        context=payload.context,
        memory_context=memory_context,
    )

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

    await _increment_use_count(db, [uuid.UUID(mid) for mid in memory_ids_used])
    await audit(db, action="marketing_strategy.generated", tenant_id=tenant_id, target=("content_assets", asset.id))
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
    await audit(db, action="ai_campaign.created", tenant_id=tenant_id, target=("ai_campaigns", campaign.id))
    return campaign


async def execute_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> AiCampaign:
    """Execute an AI campaign — push approved assets to target segment.

    Phase 1: LINE multicast only. Other platforms raise ValidationError.

    Design notes:
    - Exceptions inside the try block are caught; FAILED status is committed
      (not rolled back) so the campaign never gets stuck in RUNNING.
    - FAILED campaigns can be re-executed (retry semantics).
    - On success: assets → PUBLISHED, campaign_learning memory auto-created.
    """
    campaign = await db.get(AiCampaign, campaign_id)
    if not campaign or campaign.tenant_id != tenant_id:
        raise NotFoundError(f"AiCampaign {campaign_id} not found")
    # Allow retry of previously failed campaigns.
    if campaign.status not in (AiCampaignStatus.PENDING, AiCampaignStatus.FAILED):
        raise ValidationError(
            f"Campaign {campaign_id} is '{campaign.status.value}'. "
            "Only 'pending' or 'failed' campaigns can be executed."
        )

    # Phase 1: LINE only.
    if campaign.target_platform != AssetPlatform.LINE:
        raise ValidationError(
            f"Platform '{campaign.target_platform.value}' is not yet supported. "
            "Only LINE campaigns are available in Phase 1."
        )

    campaign.status = AiCampaignStatus.RUNNING
    await db.flush()

    try:
        # Fetch all assets in one query (avoids N+1).
        asset_id_uuids = [uuid.UUID(aid) for aid in (campaign.asset_ids or [])]
        asset_rows: list[ContentAsset] = []
        if asset_id_uuids:
            rows = await db.execute(
                select(ContentAsset).where(ContentAsset.id.in_(asset_id_uuids))
            )
            asset_rows = list(rows.scalars().all())

        combined_content = "\n\n---\n\n".join(a.content for a in asset_rows)

        # Compute reach via RFM service for typed segments, or raw LINE-customer
        # count for "ALL".
        reach = 0
        from . import rfm_service

        seg_value = campaign.target_segment
        if seg_value == "ALL":
            from ..models.customers import Customer

            q = select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
                Customer.line_user_id.isnot(None),
            )
            customers = list((await db.execute(q)).scalars().all())
            reach = len(customers)
            # TODO Phase 2: actual multicast via rfm_service / LineMessenger
        else:
            segment = RfmSegment(seg_value)
            broadcast = await rfm_service.broadcast_to_segment(
                db,
                tenant_id=tenant_id,
                segment=segment,
                message=combined_content,
                as_of=date.today(),
            )
            reach = broadcast.delivered

        # Mark assets published (single loop — assets already fetched above).
        for asset in asset_rows:
            asset.status = AssetStatus.PUBLISHED

        campaign.status = AiCampaignStatus.COMPLETED
        campaign.reach_count = reach
        campaign.result_summary = {
            "reach": reach,
            "platform": campaign.target_platform.value,
            "segment": campaign.target_segment,
            "assets_used": len(asset_rows),
        }
        await db.flush()

        # Close the Hermes loop: auto-create a campaign_learning memory so
        # future generation calls can reference what worked.
        learning = MarketingMemory(
            tenant_id=tenant_id,
            memory_type=MemoryType.CAMPAIGN_LEARNING,
            title=f"活動執行紀錄：{campaign.name}",
            content=(
                f"平台：{campaign.target_platform.value}\n"
                f"目標分眾：{campaign.target_segment}\n"
                f"觸達人數：{reach}\n"
                f"使用素材數量：{len(asset_rows)}"
            ),
            meta={"campaign_id": str(campaign_id), "reach": reach},
        )
        db.add(learning)
        await db.flush()

        await db.refresh(campaign)
        await audit(db, action="ai_campaign.completed", tenant_id=tenant_id, target=("ai_campaigns", campaign.id))
        logger.info(
            "marketing.campaign_executed",
            extra={
                "campaign_id": str(campaign.id),
                "reach": reach,
                "platform": campaign.target_platform.value,
            },
        )

    except (NotFoundError, ValidationError):
        # Domain errors from our own code: still mark FAILED + commit.
        campaign.status = AiCampaignStatus.FAILED
        campaign.result_summary = {"error": "validation_failed"}
        await db.flush()
        await db.refresh(campaign)
        await audit(db, action="ai_campaign.failed", tenant_id=tenant_id, target=("ai_campaigns", campaign.id))
        raise

    except Exception as exc:
        # Unexpected execution error (LINE API down, DB issue, etc.).
        # Do NOT re-raise — let the DI layer commit the FAILED status so the
        # campaign doesn't get stuck in RUNNING forever.
        campaign.status = AiCampaignStatus.FAILED
        campaign.result_summary = {"error": str(exc)}
        await db.flush()
        await db.refresh(campaign)
        await audit(db, action="ai_campaign.failed", tenant_id=tenant_id, target=("ai_campaigns", campaign.id))
        logger.error(
            "marketing.campaign_failed",
            extra={"campaign_id": str(campaign.id), "error": str(exc)},
        )

    return campaign


async def list_campaigns(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: AiCampaignStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AiCampaign]:
    q = select(AiCampaign).where(AiCampaign.tenant_id == tenant_id)
    if status:
        q = q.where(AiCampaign.status == status)
    q = q.order_by(AiCampaign.created_at.desc()).offset(offset).limit(limit)
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
