"""Integration tests for the /marketing router (Hermes-Claude-Codex).

Tests cover the three-layer architecture:
  - Hermes: memory CRUD
  - Claude: content generation (stub mode), asset lifecycle
  - Codex: campaign creation and execution
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.auth import AdminPrincipal, require_admin
from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Tenant
from restaurant_api.models.marketing import AssetStatus, ContentAsset
from restaurant_api.schemas.marketing import (
    AiCampaignCreate,
    ContentGenerateRequest,
    MemoryCreate,
    MemoryUpdate,
    StrategyRequest,
)
from restaurant_api.services import marketing_service

pytestmark = pytest.mark.asyncio


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
    db: AsyncSession, tenant_id: uuid.UUID
) -> ContentAsset:
    asset = await marketing_service.generate_content(
        db,
        tenant_id,
        ContentGenerateRequest(
            asset_type="line_message",  # type: ignore[arg-type]
            platform="line",  # type: ignore[arg-type]
            title="Campaign Asset",
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

    r = await client.patch(
        f"/marketing/content/{asset.id}",
        json={"status": "approved"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


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


async def test_create_and_execute_campaign(
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


async def test_list_campaigns_empty(client: httpx.AsyncClient) -> None:
    r = await client.get("/marketing/ai-campaigns")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_get_campaign_not_found(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/marketing/ai-campaigns/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_execute_nonexistent_campaign(client: httpx.AsyncClient) -> None:
    r = await client.post(f"/marketing/ai-campaigns/{uuid.uuid4()}/execute")
    assert r.status_code == 404
