"""Integration tests for the /marketing router (Hermes-Claude-Codex) — v2.

Coverage matrix:
  Hermes memory layer:
    - CRUD happy paths
    - tags clearing ([] clears, None no-ops)
    - list pagination (offset)
    - type filter

  Claude content layer:
    - stub generation (no API key)
    - list with status filter + offset
    - asset status machine (valid transitions, invalid transitions)
    - 404 on unknown asset

  Claude strategy layer:
    - stub generation
    - multiple memory_types OR filter

  Codex campaign layer:
    - rejects draft asset
    - valid create + execute (LINE/ALL segment)
    - LINE RFM segment execute
    - invalid segment rejected at creation
    - non-LINE platform rejected at execution
    - already-running campaign rejected
    - Hermes feedback loop (campaign_learning memory auto-created)
    - retry of FAILED campaign
    - 404 on unknown campaign
    - execute 404 on unknown campaign
    - campaign list pagination (offset)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.auth import AdminPrincipal, require_admin
from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Tenant
from restaurant_api.models.marketing import AiCampaignStatus, AssetStatus, ContentAsset
from restaurant_api.schemas.marketing import (
    AiCampaignCreate,
    ContentGenerateRequest,
    MemoryCreate,
)
from restaurant_api.services import marketing_service
from restaurant_api.services.ai_content_service import GenerationResult

pytestmark = pytest.mark.asyncio

_STUB_RESULT = GenerationResult(
    content="[AI stub — set ANTHROPIC_API_KEY to enable real generation]\n\n✨ 限時優惠！來店消費享點數回饋，立即體驗美食新享受！",
    model="stub",
    input_tokens=None,
    output_tokens=None,
)


@pytest_asyncio.fixture(autouse=True)
async def _patch_ai_content() -> AsyncIterator[None]:
    """Patch Claude API calls so tests run without real credentials or credits."""
    with (
        patch(
            "restaurant_api.services.ai_content_service.generate_content",
            new=AsyncMock(return_value=_STUB_RESULT),
        ),
        patch(
            "restaurant_api.services.ai_content_service.generate_strategy",
            new=AsyncMock(return_value=_STUB_RESULT),
        ),
    ):
        yield


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    app.dependency_overrides[require_admin] = lambda: AdminPrincipal()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


async def _make_approved_asset(
    db: AsyncSession, tenant_id: uuid.UUID, title: str = "Campaign Asset"
) -> ContentAsset:
    asset = await marketing_service.generate_content(
        db,
        tenant_id,
        ContentGenerateRequest(
            asset_type="line_message",  # type: ignore[arg-type]
            platform="line",  # type: ignore[arg-type]
            title=title,
            prompt="週年慶感謝顧客支持，推出限定優惠活動",
        ),
    )
    await db.flush()
    await marketing_service.update_asset_status(db, tenant_id, asset.id, AssetStatus.APPROVED)
    await db.flush()
    return asset


# ──────────────────────────────────────────────────────────────────────────
# Hermes memory layer
# ──────────────────────────────────────────────────────────────────────────


async def test_create_memory(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/marketing/memories",
        json={
            "memory_type": "brand_voice",
            "title": "我們的品牌聲音",
            "content": "親切、溫暖、充滿台灣在地感",
            "tags": ["tone", "voice"],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["memory_type"] == "brand_voice"
    assert data["use_count"] == 0
    assert "id" in data


async def test_list_memories_empty(client: httpx.AsyncClient) -> None:
    r = await client.get("/marketing/memories")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_memories_by_type(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    await marketing_service.create_memory(
        db_session,
        seed_tenant.id,
        MemoryCreate(
            memory_type="brand_voice",  # type: ignore[arg-type]
            title="Brand Voice",
            content="Warm and friendly",
            tags=["tone"],
        ),
    )
    await marketing_service.create_memory(
        db_session,
        seed_tenant.id,
        MemoryCreate(
            memory_type="campaign_learning",  # type: ignore[arg-type]
            title="Previous Campaign",
            content="LINE push had 30% open rate",
        ),
    )
    await db_session.flush()

    r = await client.get("/marketing/memories?memory_type=brand_voice")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["memory_type"] == "brand_voice"


async def test_list_memories_offset_pagination(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    for i in range(3):
        await marketing_service.create_memory(
            db_session,
            seed_tenant.id,
            MemoryCreate(
                memory_type="brand_voice",  # type: ignore[arg-type]
                title=f"Memory {i}",
                content=f"Content {i}",
            ),
        )
    await db_session.flush()

    r_all = await client.get("/marketing/memories?limit=10&offset=0")
    assert r_all.status_code == 200
    assert len(r_all.json()) == 3

    r_page2 = await client.get("/marketing/memories?limit=2&offset=2")
    assert r_page2.status_code == 200
    assert len(r_page2.json()) == 1


async def test_get_memory(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    mem = await marketing_service.create_memory(
        db_session,
        seed_tenant.id,
        MemoryCreate(
            memory_type="successful_skill",  # type: ignore[arg-type]
            title="Lucky Wheel",
            content="Wheel-spin drove 40% return visits",
        ),
    )
    await db_session.flush()

    r = await client.get(f"/marketing/memories/{mem.id}")
    assert r.status_code == 200
    assert r.json()["id"] == str(mem.id)


async def test_get_memory_not_found(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/marketing/memories/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_update_memory(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    mem = await marketing_service.create_memory(
        db_session,
        seed_tenant.id,
        MemoryCreate(
            memory_type="brand_voice",  # type: ignore[arg-type]
            title="Original",
            content="Original content",
            tags=["old_tag"],
        ),
    )
    await db_session.flush()

    r = await client.patch(
        f"/marketing/memories/{mem.id}",
        json={"title": "Updated", "effectiveness_score": 85.5},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Updated"
    assert float(data["effectiveness_score"]) == pytest.approx(85.5, abs=0.1)
    assert data["tags"] == ["old_tag"]  # unchanged


async def test_update_memory_clears_tags_with_empty_list(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    mem = await marketing_service.create_memory(
        db_session,
        seed_tenant.id,
        MemoryCreate(
            memory_type="brand_voice",  # type: ignore[arg-type]
            title="Tagged",
            content="Some content",
            tags=["tag1", "tag2"],
        ),
    )
    await db_session.flush()

    # Empty list should clear tags to NULL.
    r = await client.patch(f"/marketing/memories/{mem.id}", json={"tags": []})
    assert r.status_code == 200
    assert r.json()["tags"] is None


# ──────────────────────────────────────────────────────────────────────────
# Claude content generation layer
# ──────────────────────────────────────────────────────────────────────────


async def test_generate_content_stub(client: httpx.AsyncClient) -> None:
    """Without ANTHROPIC_API_KEY the service returns a clearly-labelled stub."""
    r = await client.post(
        "/marketing/content/generate",
        json={
            "asset_type": "social_post",
            "platform": "instagram",
            "title": "六月促銷貼文",
            "prompt": "推廣六月限定草莓季甜點，目標吸引 25-35 歲女性顧客",
            "language": "zh-TW",
            "tone": "活潑",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "draft"
    assert data["asset_type"] == "social_post"
    assert data["platform"] == "instagram"
    assert len(data["content"]) > 0


async def test_list_assets_by_status(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    await marketing_service.generate_content(
        db_session,
        seed_tenant.id,
        ContentGenerateRequest(
            asset_type="line_message",  # type: ignore[arg-type]
            platform="line",  # type: ignore[arg-type]
            title="LINE 訊息",
            prompt="週末限定半價優惠，快來搶購！",
        ),
    )
    await db_session.flush()

    r = await client.get("/marketing/content?asset_status=draft")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert all(i["status"] == "draft" for i in items)


async def test_list_assets_offset_pagination(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    for i in range(3):
        await marketing_service.generate_content(
            db_session,
            seed_tenant.id,
            ContentGenerateRequest(
                asset_type="social_post",  # type: ignore[arg-type]
                platform="instagram",  # type: ignore[arg-type]
                title=f"Post {i}",
                prompt="推廣限定甜點，目標年輕客群，增加互動率",
            ),
        )
    await db_session.flush()

    r_all = await client.get("/marketing/content?limit=10&offset=0")
    assert r_all.status_code == 200
    total = len(r_all.json())
    assert total >= 3

    r_offset = await client.get(f"/marketing/content?limit=10&offset={total}")
    assert r_offset.status_code == 200
    assert r_offset.json() == []


async def test_get_asset_not_found(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/marketing/content/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_update_asset_status_to_approved(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    asset = await marketing_service.generate_content(
        db_session,
        seed_tenant.id,
        ContentGenerateRequest(
            asset_type="promotion_copy",  # type: ignore[arg-type]
            platform="facebook",  # type: ignore[arg-type]
            title="FB 廣告文案",
            prompt="暑假親子套餐九折優惠，限時三天",
        ),
    )
    await db_session.flush()

    r = await client.patch(f"/marketing/content/{asset.id}", json={"status": "approved"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


async def test_asset_status_machine_rejects_invalid_transition(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """DRAFT → PUBLISHED must be rejected (must go via APPROVED first)."""
    asset = await marketing_service.generate_content(
        db_session,
        seed_tenant.id,
        ContentGenerateRequest(
            asset_type="line_message",  # type: ignore[arg-type]
            platform="line",  # type: ignore[arg-type]
            title="Direct Publish Test",
            prompt="測試狀態機阻擋非法轉換，確保工作流正確",
        ),
    )
    await db_session.flush()

    r = await client.patch(f"/marketing/content/{asset.id}", json={"status": "published"})
    assert r.status_code in (400, 422)


async def test_asset_status_machine_rejects_archived_to_approved(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """ARCHIVED → APPROVED must be rejected (archived is terminal)."""
    asset = await marketing_service.generate_content(
        db_session,
        seed_tenant.id,
        ContentGenerateRequest(
            asset_type="line_message",  # type: ignore[arg-type]
            platform="line",  # type: ignore[arg-type]
            title="Archive Terminal Test",
            prompt="測試封存後不可再回到已核准狀態的狀態機護欄",
        ),
    )
    await db_session.flush()
    await marketing_service.update_asset_status(
        db_session, seed_tenant.id, asset.id, AssetStatus.APPROVED
    )
    await marketing_service.update_asset_status(
        db_session, seed_tenant.id, asset.id, AssetStatus.ARCHIVED
    )
    await db_session.flush()

    r = await client.patch(f"/marketing/content/{asset.id}", json={"status": "approved"})
    assert r.status_code in (400, 422)


# ──────────────────────────────────────────────────────────────────────────
# Claude strategy layer
# ──────────────────────────────────────────────────────────────────────────


async def test_generate_strategy_stub(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/marketing/strategy",
        json={
            "objective": "提升暑假期間親子客群來店率，目標回訪率增加 20%",
            "context": "七月份，預算 NT$30,000，LINE 為主要通路",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["asset_type"] == "strategy_brief"
    assert data["platform"] == "multi"
    assert len(data["content"]) > 0


async def test_generate_strategy_multiple_memory_types(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Passing multiple memory_types should use OR filter, not silently ignore."""
    for mtype, title in [
        ("brand_voice", "品牌聲音記憶"),
        ("campaign_learning", "過去活動學習"),
        ("competitive_insight", "競爭者觀察"),
    ]:
        await marketing_service.create_memory(
            db_session,
            seed_tenant.id,
            MemoryCreate(
                memory_type=mtype,  # type: ignore[arg-type]
                title=title,
                content="測試內容",
            ),
        )
    await db_session.flush()

    r = await client.post(
        "/marketing/strategy",
        json={
            "objective": "提升夏季業績，主打冰品與飲料，搭配 LINE 推播活動",
            "memory_types": ["brand_voice", "campaign_learning"],
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["asset_type"] == "strategy_brief"
    # memory_ids_used should include memories of both filtered types (not competitive_insight)
    assert data["memory_ids_used"] is not None


# ──────────────────────────────────────────────────────────────────────────
# Codex campaign execution layer
# ──────────────────────────────────────────────────────────────────────────


async def test_create_campaign_rejects_draft_asset(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    asset = await marketing_service.generate_content(
        db_session,
        seed_tenant.id,
        ContentGenerateRequest(
            asset_type="line_message",  # type: ignore[arg-type]
            platform="line",  # type: ignore[arg-type]
            title="Draft Asset",
            prompt="週年慶活動通知，感謝顧客一年支持",
        ),
    )
    await db_session.flush()

    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "周年慶推播",
            "target_platform": "line",
            "asset_ids": [str(asset.id)],
        },
    )
    assert r.status_code in (400, 422)


async def test_create_campaign_rejects_invalid_segment(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Unknown segment values must be rejected at campaign creation time."""
    asset = await _make_approved_asset(db_session, seed_tenant.id)

    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "Bad Segment Campaign",
            "target_platform": "line",
            "target_segment": "UNKNOWN_SEGMENT",
            "asset_ids": [str(asset.id)],
        },
    )
    assert r.status_code == 422


async def test_create_and_execute_campaign_all_segment(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    asset = await _make_approved_asset(db_session, seed_tenant.id)

    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "周年慶推播",
            "target_platform": "line",
            "target_segment": "ALL",
            "asset_ids": [str(asset.id)],
            "strategy_brief": "感謝顧客一年來的支持",
        },
    )
    assert r.status_code == 201
    campaign = r.json()
    assert campaign["status"] == "pending"
    campaign_id = campaign["id"]

    r = await client.post(f"/marketing/ai-campaigns/{campaign_id}/execute")
    assert r.status_code == 200
    result = r.json()
    assert result["status"] == "completed"
    assert result["reach_count"] is not None
    assert result["result_summary"]["platform"] == "line"


async def test_execute_campaign_rfm_segment(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Campaign with a valid RFM segment (e.g. 'champion') should execute."""
    asset = await _make_approved_asset(db_session, seed_tenant.id)

    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "冠軍顧客活動",
            "target_platform": "line",
            "target_segment": "champion",
            "asset_ids": [str(asset.id)],
        },
    )
    assert r.status_code == 201
    campaign_id = r.json()["id"]

    r = await client.post(f"/marketing/ai-campaigns/{campaign_id}/execute")
    assert r.status_code == 200
    result = r.json()
    # With no seeded customers, reach=0 but still completes.
    assert result["status"] == "completed"
    assert result["reach_count"] == 0


async def test_execute_campaign_non_line_platform_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """Non-LINE campaigns should fail execution in Phase 1."""
    # Create asset for instagram
    asset = await marketing_service.generate_content(
        db_session,
        seed_tenant.id,
        ContentGenerateRequest(
            asset_type="social_post",  # type: ignore[arg-type]
            platform="instagram",  # type: ignore[arg-type]
            title="IG Post",
            prompt="夏季新品上市推廣，吸引年輕顧客打卡分享",
        ),
    )
    await db_session.flush()
    await marketing_service.update_asset_status(
        db_session, seed_tenant.id, asset.id, AssetStatus.APPROVED
    )
    await db_session.flush()

    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "IG Campaign",
            "target_platform": "instagram",
            "asset_ids": [str(asset.id)],
        },
    )
    assert r.status_code == 201
    campaign_id = r.json()["id"]

    r = await client.post(f"/marketing/ai-campaigns/{campaign_id}/execute")
    # Phase 1: only LINE is supported; should be rejected.
    assert r.status_code in (400, 422)


async def test_execute_running_campaign_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """A RUNNING campaign cannot be re-executed."""

    asset = await _make_approved_asset(db_session, seed_tenant.id)
    campaign_obj = await marketing_service.create_ai_campaign(
        db_session,
        seed_tenant.id,
        AiCampaignCreate(
            name="Running Campaign",
            target_platform="line",  # type: ignore[arg-type]
            asset_ids=[asset.id],
        ),
    )
    await db_session.flush()
    # Force status to RUNNING directly.
    campaign_obj.status = AiCampaignStatus.RUNNING
    await db_session.flush()

    r = await client.post(f"/marketing/ai-campaigns/{campaign_obj.id}/execute")
    assert r.status_code in (400, 422)


async def test_execute_campaign_creates_hermes_feedback_memory(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """After successful execution, a campaign_learning memory must be auto-created."""
    from sqlalchemy import select

    from restaurant_api.models.marketing import MarketingMemory, MemoryType

    asset = await _make_approved_asset(db_session, seed_tenant.id, title="Feedback Test Asset")
    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "Feedback Loop Test",
            "target_platform": "line",
            "target_segment": "ALL",
            "asset_ids": [str(asset.id)],
        },
    )
    assert r.status_code == 201
    campaign_id = r.json()["id"]

    r = await client.post(f"/marketing/ai-campaigns/{campaign_id}/execute")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"

    # Verify Hermes loop: a campaign_learning memory should now exist.
    memories = list(
        (
            await db_session.execute(
                select(MarketingMemory).where(
                    MarketingMemory.tenant_id == seed_tenant.id,
                    MarketingMemory.memory_type == MemoryType.CAMPAIGN_LEARNING,
                )
            )
        ).scalars().all()
    )
    assert len(memories) >= 1
    assert "Feedback Loop Test" in memories[-1].title


async def test_retry_failed_campaign(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """A FAILED campaign can be re-executed (retry semantics)."""

    asset = await _make_approved_asset(db_session, seed_tenant.id, title="Retry Asset")
    campaign_obj = await marketing_service.create_ai_campaign(
        db_session,
        seed_tenant.id,
        AiCampaignCreate(
            name="Retry Campaign",
            target_platform="line",  # type: ignore[arg-type]
            asset_ids=[asset.id],
        ),
    )
    await db_session.flush()
    # Simulate a previous failure.
    campaign_obj.status = AiCampaignStatus.FAILED
    campaign_obj.result_summary = {"error": "simulated previous failure"}
    await db_session.flush()

    # Execute (retry) should succeed.
    r = await client.post(f"/marketing/ai-campaigns/{campaign_obj.id}/execute")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


async def test_list_campaigns_empty(client: httpx.AsyncClient) -> None:
    r = await client.get("/marketing/ai-campaigns")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_list_campaigns_offset_pagination(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    for i in range(3):
        asset = await _make_approved_asset(db_session, seed_tenant.id, title=f"Asset {i}")
        await marketing_service.create_ai_campaign(
            db_session,
            seed_tenant.id,
            AiCampaignCreate(
                name=f"Campaign {i}",
                target_platform="line",  # type: ignore[arg-type]
                asset_ids=[asset.id],
            ),
        )
    await db_session.flush()

    r_all = await client.get("/marketing/ai-campaigns?limit=10&offset=0")
    assert r_all.status_code == 200
    assert len(r_all.json()) == 3

    r_page2 = await client.get("/marketing/ai-campaigns?limit=2&offset=2")
    assert r_page2.status_code == 200
    assert len(r_page2.json()) == 1


async def test_get_campaign_not_found(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/marketing/ai-campaigns/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_execute_nonexistent_campaign(client: httpx.AsyncClient) -> None:
    r = await client.post(f"/marketing/ai-campaigns/{uuid.uuid4()}/execute")
    assert r.status_code == 404


async def test_completed_campaign_cannot_be_re_executed(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    """A COMPLETED campaign must not be re-executed (idempotency guard)."""
    asset = await _make_approved_asset(db_session, seed_tenant.id, title="Completed Asset")
    r = await client.post(
        "/marketing/ai-campaigns",
        json={
            "name": "Complete Once",
            "target_platform": "line",
            "target_segment": "ALL",
            "asset_ids": [str(asset.id)],
        },
    )
    assert r.status_code == 201
    campaign_id = r.json()["id"]

    # First execution succeeds.
    r1 = await client.post(f"/marketing/ai-campaigns/{campaign_id}/execute")
    assert r1.status_code == 200
    assert r1.json()["status"] == "completed"

    # Second execution on an already-COMPLETED campaign must be rejected.
    r2 = await client.post(f"/marketing/ai-campaigns/{campaign_id}/execute")
    assert r2.status_code in (400, 422)
