"""P8.1 — 多語系菜單 (structured, staff-entered translations)."""

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


async def _item(client: httpx.AsyncClient, store: str, name: str = "牛肉麵") -> dict:
    r = await client.post(
        "/menu/items",
        json={"store_id": store, "sku": f"ML{uuid.uuid4().hex[:6]}", "name": name, "price": "180",
              "description": "紅燒湯頭"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_set_translation_and_fetch_localized(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    item = await _item(client, store)

    r = await client.put(
        f"/menu/items/{item['id']}/translations",
        json={"lang": "en", "name": "Beef Noodle Soup", "description": "Braised beef broth"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["translations"]["en"]["name"] == "Beef Noodle Soup"

    localized = (await client.get(f"/menu/items/{item['id']}", params={"lang": "en"})).json()
    assert localized["name"] == "Beef Noodle Soup"
    assert localized["description"] == "Braised beef broth"

    # zh-Hant default unaffected when no lang requested
    original = (await client.get(f"/menu/items/{item['id']}")).json()
    assert original["name"] == "牛肉麵"


async def test_missing_translation_falls_back_to_zh(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    item = await _item(client, store)
    r = (await client.get(f"/menu/items/{item['id']}", params={"lang": "ja"})).json()
    assert r["name"] == "牛肉麵"  # no ja translation set -> falls back


async def test_translation_missing_description_falls_back(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    item = await _item(client, store)
    await client.put(
        f"/menu/items/{item['id']}/translations",
        json={"lang": "en", "name": "Beef Noodle Soup"},  # no description
    )
    r = (await client.get(f"/menu/items/{item['id']}", params={"lang": "en"})).json()
    assert r["name"] == "Beef Noodle Soup"
    assert r["description"] == "紅燒湯頭"  # falls back to zh-Hant


async def test_invalid_lang_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    item = await _item(client, store)
    r = await client.put(
        f"/menu/items/{item['id']}/translations",
        json={"lang": "fr", "name": "Soupe de nouilles"},  # not in SUPPORTED_LANGS
    )
    assert r.status_code == 422, r.text


async def test_list_items_applies_lang_per_item(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    a = await _item(client, store, "牛肉麵")
    await _item(client, store, "滷肉飯")  # left untranslated
    await client.put(f"/menu/items/{a['id']}/translations", json={"lang": "en", "name": "Beef Noodle Soup"})

    rows = (await client.get("/menu/items", params={"store_id": store, "lang": "en"})).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[a["id"]]["name"] == "Beef Noodle Soup"


async def test_overwrite_translation_replaces_previous(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    item = await _item(client, store)
    await client.put(f"/menu/items/{item['id']}/translations", json={"lang": "en", "name": "V1"})
    r = await client.put(f"/menu/items/{item['id']}/translations", json={"lang": "en", "name": "V2"})
    assert r.json()["translations"]["en"]["name"] == "V2"
