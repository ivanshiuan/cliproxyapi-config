"""Integration tests for the ``/tables`` router (桌位管理).

Covers
------
- Create with defaults; duplicate live name in same store → 409.
- Soft delete (204) → name freed → recreate same name succeeds.
- List: default hides inactive + soft-deleted; ``include_inactive`` flag;
  ordering by (sort_order, name); ``store_id`` scoping.
- PATCH each field; renaming onto another live table's name → 409.
- Validation: capacity=0 and blank name → 422.
- Unknown table id → 404 for PATCH and DELETE.
- Same name in a *different* store is allowed.

All queries are driven through the seeded tenant/store fixtures — no
whole-table scans, so demo/seed data can't interfere.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Store, Tenant
from restaurant_api.routers import tables as tables_router  # noqa: F401 (self-mounts)

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


@pytest_asyncio.fixture
async def second_store(db_session: AsyncSession, seed_tenant: Tenant) -> Store:
    s = Store(
        tenant_id=seed_tenant.id,
        name="Second Store",
        address="Second Address",
        phone="02-1111-1111",
        opened_on=date(2026, 2, 1),
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


def _payload(store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"store_id": str(store_id), "name": "A1"}
    base.update(overrides)
    return base


async def _create(
    client: httpx.AsyncClient, store_id: uuid.UUID, **overrides: Any
) -> dict[str, Any]:
    resp = await client.post("/tables", json=_payload(store_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ──────────────────────────────────────────────────────────────────────────
# Create
# ──────────────────────────────────────────────────────────────────────────


async def test_create_table_returns_defaults(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    body = await _create(client, seed_store.id, name="吧台3")
    assert body["name"] == "吧台3"
    assert body["store_id"] == str(seed_store.id)
    assert body["capacity"] == 4
    assert body["sort_order"] == 0
    assert body["is_active"] is True
    assert body["deleted_at"] is None


async def test_create_duplicate_live_name_same_store_409(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    await _create(client, seed_store.id, name="A1")
    resp = await client.post("/tables", json=_payload(seed_store.id, name="A1"))
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_same_name_in_different_store_allowed(
    client: httpx.AsyncClient, seed_store: Store, second_store: Store
) -> None:
    await _create(client, seed_store.id, name="A1")
    body = await _create(client, second_store.id, name="A1")
    assert body["store_id"] == str(second_store.id)


async def test_recreate_same_name_after_soft_delete_succeeds(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    first = await _create(client, seed_store.id, name="A1")
    del_resp = await client.delete(f"/tables/{first['id']}")
    assert del_resp.status_code == 204, del_resp.text
    reborn = await _create(client, seed_store.id, name="A1")
    assert reborn["id"] != first["id"]


# ──────────────────────────────────────────────────────────────────────────
# List
# ──────────────────────────────────────────────────────────────────────────


async def test_list_default_hides_inactive_and_deleted(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    active = await _create(client, seed_store.id, name="A1")
    inactive = await _create(client, seed_store.id, name="A2")
    deleted = await _create(client, seed_store.id, name="A3")
    await client.patch(f"/tables/{inactive['id']}", json={"is_active": False})
    await client.delete(f"/tables/{deleted['id']}")

    resp = await client.get("/tables", params={"store_id": str(seed_store.id)})
    assert resp.status_code == 200, resp.text
    ids = [row["id"] for row in resp.json()]
    assert ids == [active["id"]]


async def test_list_include_inactive_shows_hidden_but_not_deleted(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    active = await _create(client, seed_store.id, name="A1")
    inactive = await _create(client, seed_store.id, name="A2")
    deleted = await _create(client, seed_store.id, name="A3")
    await client.patch(f"/tables/{inactive['id']}", json={"is_active": False})
    await client.delete(f"/tables/{deleted['id']}")

    resp = await client.get(
        "/tables",
        params={"store_id": str(seed_store.id), "include_inactive": "true"},
    )
    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()}
    assert ids == {active["id"], inactive["id"]}


async def test_list_orders_by_sort_order_then_name(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    await _create(client, seed_store.id, name="B1", sort_order=2)
    await _create(client, seed_store.id, name="A2", sort_order=1)
    await _create(client, seed_store.id, name="A1", sort_order=1)

    resp = await client.get("/tables", params={"store_id": str(seed_store.id)})
    assert resp.status_code == 200, resp.text
    assert [row["name"] for row in resp.json()] == ["A1", "A2", "B1"]


async def test_list_scopes_to_requested_store(
    client: httpx.AsyncClient, seed_store: Store, second_store: Store
) -> None:
    await _create(client, seed_store.id, name="A1")
    other = await _create(client, second_store.id, name="Z9")

    resp = await client.get("/tables", params={"store_id": str(second_store.id)})
    assert resp.status_code == 200, resp.text
    assert [row["id"] for row in resp.json()] == [other["id"]]


# ──────────────────────────────────────────────────────────────────────────
# Patch
# ──────────────────────────────────────────────────────────────────────────


async def test_patch_updates_each_field(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    created = await _create(client, seed_store.id, name="A1")
    resp = await client.patch(
        f"/tables/{created['id']}",
        json={
            "name": "戶外2",
            "zone": "戶外",
            "capacity": 6,
            "sort_order": 9,
            "is_active": False,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "戶外2"
    assert body["zone"] == "戶外"
    assert body["capacity"] == 6
    assert body["sort_order"] == 9
    assert body["is_active"] is False


async def test_patch_rename_onto_live_name_409(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    await _create(client, seed_store.id, name="A1")
    other = await _create(client, seed_store.id, name="A2")
    resp = await client.patch(f"/tables/{other['id']}", json={"name": "A1"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_patch_unknown_table_404(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.patch(f"/tables/{uuid.uuid4()}", json={"capacity": 2})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# ──────────────────────────────────────────────────────────────────────────
# Delete + validation
# ──────────────────────────────────────────────────────────────────────────


async def test_delete_unknown_table_404(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.delete(f"/tables/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


async def test_delete_twice_second_is_404(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    created = await _create(client, seed_store.id, name="A1")
    assert (await client.delete(f"/tables/{created['id']}")).status_code == 204
    resp = await client.delete(f"/tables/{created['id']}")
    assert resp.status_code == 404, resp.text


async def test_create_capacity_zero_422(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.post(
        "/tables", json=_payload(seed_store.id, name="A1", capacity=0)
    )
    assert resp.status_code == 422, resp.text


async def test_create_blank_name_422(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    resp = await client.post("/tables", json=_payload(seed_store.id, name="   "))
    assert resp.status_code == 422, resp.text
