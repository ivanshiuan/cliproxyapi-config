"""Integration tests for the /menu router (P1.2 catalog CRUD).

Real PG via the ``client`` fixture (ASGITransport). Every assertion scopes to
the fixture's seed_store so we never collide with seed/demo data.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

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
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> AsyncIterator[httpx.AsyncClient]:
    """Menu router client with the tenant pinned to the fixture's seed_tenant."""

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


async def _mk_category(client: httpx.AsyncClient, store_id: str, name: str = "主餐") -> dict:
    resp = await client.post(
        "/menu/categories", json={"name": name, "store_id": store_id, "sort_order": 1}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_and_list_category(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    cat = await _mk_category(client, str(seed_store.id))
    assert cat["name"] == "主餐"
    assert cat["is_active"] is True

    resp = await client.get("/menu/categories", params={"store_id": str(seed_store.id)})
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert "主餐" in names


async def test_create_item_with_string_price_and_reject_float(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    cat = await _mk_category(client, str(seed_store.id))
    # string price accepted
    ok = await client.post(
        "/menu/items",
        json={
            "sku": "A-001",
            "name": "牛肉麵",
            "price": "180",
            "store_id": str(seed_store.id),
            "category_id": cat["id"],
            "allergens": ["wheat", "soy"],
        },
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert Decimal(body["price"]) == Decimal("180")
    assert body["allergens"] == ["wheat", "soy"]

    # round-trips through the DB at Money(14,4) precision on re-fetch
    refetched = await client.get(f"/menu/items/{body['id']}")
    assert Decimal(refetched.json()["price"]) == Decimal("180.0000")

    # float price rejected (money-safety rule)
    bad = await client.post(
        "/menu/items",
        json={"sku": "A-002", "name": "湯", "price": 60.5, "store_id": str(seed_store.id)},
    )
    assert bad.status_code == 422


async def test_duplicate_sku_conflicts(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    payload = {"sku": "DUP-1", "name": "A", "price": "50", "store_id": str(seed_store.id)}
    first = await client.post("/menu/items", json=payload)
    assert first.status_code == 201
    second = await client.post(
        "/menu/items", json={**payload, "name": "B"}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"


async def test_update_item_partial(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    item = (
        await client.post(
            "/menu/items",
            json={"sku": "U-1", "name": "原價", "price": "100", "store_id": str(seed_store.id)},
        )
    ).json()

    resp = await client.patch(
        f"/menu/items/{item['id']}", json={"price": "120", "is_available": False}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["price"]) == Decimal("120")
    assert body["is_available"] is False
    assert body["name"] == "原價"  # untouched


async def test_soft_delete_item_hides_from_list_and_reuses_sku(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    item = (
        await client.post(
            "/menu/items",
            json={"sku": "SD-1", "name": "臨時", "price": "80", "store_id": str(seed_store.id)},
        )
    ).json()

    dele = await client.delete(f"/menu/items/{item['id']}")
    assert dele.status_code == 204

    listing = await client.get("/menu/items", params={"store_id": str(seed_store.id)})
    assert item["id"] not in [i["id"] for i in listing.json()]

    # 404 on the deleted item
    gone = await client.get(f"/menu/items/{item['id']}")
    assert gone.status_code == 404

    # SKU freed for reuse
    reuse = await client.post(
        "/menu/items",
        json={"sku": "SD-1", "name": "新版", "price": "90", "store_id": str(seed_store.id)},
    )
    assert reuse.status_code == 201


async def test_available_only_filter(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    a = (
        await client.post(
            "/menu/items",
            json={"sku": "AV-1", "name": "供應", "price": "10", "store_id": str(seed_store.id)},
        )
    ).json()
    b = (
        await client.post(
            "/menu/items",
            json={
                "sku": "AV-2",
                "name": "停售",
                "price": "10",
                "store_id": str(seed_store.id),
                "is_available": False,
            },
        )
    ).json()

    resp = await client.get(
        "/menu/items", params={"store_id": str(seed_store.id), "available_only": True}
    )
    ids = [i["id"] for i in resp.json()]
    assert a["id"] in ids
    assert b["id"] not in ids


async def test_item_with_unknown_category_404(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.post(
        "/menu/items",
        json={
            "sku": "NC-1",
            "name": "X",
            "price": "10",
            "store_id": str(seed_store.id),
            "category_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404
