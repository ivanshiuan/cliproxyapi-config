"""P8.1 — 多語系菜單 overlay on the customer table-QR context (/t/{token}/context)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Store, Tenant

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, seed_tenant: Tenant
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_context_menu_localized_when_lang_requested(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = (await client.post("/tables", json={"store_id": store, "name": f"QL-{uuid.uuid4().hex[:4]}"})).json()
    it = (
        await client.post(
            "/menu/items",
            json={"store_id": store, "sku": f"QL{uuid.uuid4().hex[:6]}", "name": "牛肉麵", "price": "180"},
        )
    ).json()
    await client.put(f"/menu/items/{it['id']}/translations", json={"lang": "en", "name": "Beef Noodle Soup"})

    ctx_en = (await client.get(f"/t/{t['qr_token']}/context", params={"lang": "en"})).json()
    menu_item = next(m for m in ctx_en["menu"] if m["id"] == it["id"])
    assert menu_item["name"] == "Beef Noodle Soup"

    ctx_default = (await client.get(f"/t/{t['qr_token']}/context")).json()
    menu_item_zh = next(m for m in ctx_default["menu"] if m["id"] == it["id"])
    assert menu_item_zh["name"] == "牛肉麵"


async def test_context_untranslated_item_falls_back(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    t = (await client.post("/tables", json={"store_id": store, "name": f"QL-{uuid.uuid4().hex[:4]}"})).json()
    await client.post(
        "/menu/items",
        json={"store_id": store, "sku": f"QL{uuid.uuid4().hex[:6]}", "name": "白飯", "price": "20"},
    )
    ctx = (await client.get(f"/t/{t['qr_token']}/context", params={"lang": "ja"})).json()
    assert any(m["name"] == "白飯" for m in ctx["menu"])
