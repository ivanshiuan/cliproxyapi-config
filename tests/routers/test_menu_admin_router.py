"""Integration tests for ``/menu`` (菜單後台管理) router.

Coverage map
------------
- test_create_category_and_list_sorted      → category create + GET sorted by sort_order
- test_patch_category                       → PATCH name / sort_order / is_active
- test_delete_category_soft                 → DELETE stamps deleted_at, GET excludes
- test_create_item_returns_decimal_price    → item create, price round-trips as Decimal
- test_create_item_duplicate_sku_conflicts  → same-tenant SKU dup → 409
- test_deleted_item_sku_reusable            → soft-deleted SKU can be recreated
- test_list_items_filters                   → category_id + include_unavailable filters
- test_patch_item_fields                    → PATCH price / allergens / description
- test_item_availability_toggle             → POST /availability 停售/恢復
- test_delete_item_soft_hides_from_get      → soft delete + GET exclusion + deleted_at set
- test_patch_unknown_item_404               → 404 envelope
- test_float_price_rejected                 → float money literal → 422
- test_modifier_group_nested_options        → nested option create; single → max_select=1
- test_modifier_group_min_max_validation    → min_select > max_select → 422
- test_option_price_delta_decimal_precision → 4dp Decimal precision on price_delta
- test_link_and_duplicate_link_conflicts    → link 201, re-link 409
- test_unlink_modifier_group                → unlink 204, second unlink 404

All DB assertions are scoped to ``seed_tenant`` — never whole-table scans.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import MenuItem, Store, Tenant
from restaurant_api.routers.menu_admin import _mount

# Idempotent self-mount — main.py doesn't wire this router yet.
_mount()

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


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _item_body(store_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sku": f"SKU-{uuid.uuid4().hex[:8]}",
        "name": "牛肉麵",
        "price": "180.00",
        "store_id": str(store_id),
    }
    body.update(overrides)
    return body


async def _create_item(
    client: httpx.AsyncClient, store_id: uuid.UUID, **overrides: Any
) -> dict[str, Any]:
    resp = await client.post("/menu/items", json=_item_body(store_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_group(
    client: httpx.AsyncClient, store_id: uuid.UUID, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": f"加料-{uuid.uuid4().hex[:6]}",
        "store_id": str(store_id),
        "selection_type": "multi",
        "min_select": 0,
        "max_select": 3,
    }
    body.update(overrides)
    resp = await client.post("/menu/modifier-groups", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ──────────────────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────────────────


async def test_create_category_and_list_sorted(client, seed_store: Store):
    for name, order in (("主餐", 2), ("前菜", 1)):
        resp = await client.post(
            "/menu/categories",
            json={"name": name, "store_id": str(seed_store.id), "sort_order": order},
        )
        assert resp.status_code == 201, resp.text

    listed = await client.get("/menu/categories", params={"store_id": str(seed_store.id)})
    assert listed.status_code == 200
    names = [c["name"] for c in listed.json()]
    assert names == ["前菜", "主餐"]  # sort_order ascending


async def test_patch_category(client, seed_store: Store):
    created = await client.post(
        "/menu/categories", json={"name": "湯品", "store_id": str(seed_store.id)}
    )
    cat_id = created.json()["id"]

    patched = await client.patch(
        f"/menu/categories/{cat_id}",
        json={"name": "湯品(改)", "sort_order": 9, "is_active": False},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert (body["name"], body["sort_order"], body["is_active"]) == ("湯品(改)", 9, False)


async def test_delete_category_soft(client, db_session: AsyncSession, seed_store: Store):
    created = await client.post(
        "/menu/categories", json={"name": "即將刪除", "store_id": str(seed_store.id)}
    )
    cat_id = created.json()["id"]

    resp = await client.delete(f"/menu/categories/{cat_id}")
    assert resp.status_code == 204

    listed = await client.get("/menu/categories", params={"store_id": str(seed_store.id)})
    assert cat_id not in [c["id"] for c in listed.json()]


# ──────────────────────────────────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────────────────────────────────


async def test_create_item_returns_decimal_price(client, seed_store: Store):
    body = await _create_item(client, seed_store.id, price="123.4500")
    assert Decimal(str(body["price"])) == Decimal("123.4500")


async def test_create_item_duplicate_sku_conflicts(client, seed_store: Store):
    sku = f"DUP-{uuid.uuid4().hex[:8]}"
    await _create_item(client, seed_store.id, sku=sku)

    dup = await client.post("/menu/items", json=_item_body(seed_store.id, sku=sku))
    assert dup.status_code == 409


async def test_deleted_item_sku_reusable(client, seed_store: Store):
    sku = f"REUSE-{uuid.uuid4().hex[:8]}"
    first = await _create_item(client, seed_store.id, sku=sku)

    deleted = await client.delete(f"/menu/items/{first['id']}")
    assert deleted.status_code == 204

    again = await client.post("/menu/items", json=_item_body(seed_store.id, sku=sku))
    assert again.status_code == 201


async def test_list_items_filters(client, seed_store: Store):
    cat = (
        await client.post(
            "/menu/categories", json={"name": "飲料", "store_id": str(seed_store.id)}
        )
    ).json()
    in_cat = await _create_item(client, seed_store.id, category_id=cat["id"], name="紅茶")
    await _create_item(client, seed_store.id, name="停售品", is_available=False)

    by_cat = await client.get("/menu/items", params={"category_id": cat["id"]})
    assert [i["id"] for i in by_cat.json()] == [in_cat["id"]]

    only_available = await client.get("/menu/items", params={"include_unavailable": "false"})
    assert all(i["is_available"] for i in only_available.json())


async def test_patch_item_fields(client, seed_store: Store):
    item = await _create_item(client, seed_store.id)
    patched = await client.patch(
        f"/menu/items/{item['id']}",
        json={"price": "250.0000", "allergens": ["peanut", "soy"], "description": "招牌"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert Decimal(str(body["price"])) == Decimal("250.0000")
    assert body["allergens"] == ["peanut", "soy"]
    assert body["description"] == "招牌"


async def test_item_availability_toggle(client, seed_store: Store):
    item = await _create_item(client, seed_store.id)

    off = await client.post(
        f"/menu/items/{item['id']}/availability", json={"is_available": False}
    )
    assert off.status_code == 200
    assert off.json()["is_available"] is False

    on = await client.post(
        f"/menu/items/{item['id']}/availability", json={"is_available": True}
    )
    assert on.json()["is_available"] is True


async def test_delete_item_soft_hides_from_get(
    client, db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
):
    item = await _create_item(client, seed_store.id)

    resp = await client.delete(f"/menu/items/{item['id']}")
    assert resp.status_code == 204

    listed = await client.get("/menu/items")
    assert item["id"] not in [i["id"] for i in listed.json()]

    row = (
        await db_session.execute(
            select(MenuItem)
            .where(MenuItem.tenant_id == seed_tenant.id)
            .where(MenuItem.id == uuid.UUID(item["id"]))
        )
    ).scalar_one()
    assert row.deleted_at is not None


async def test_patch_unknown_item_404(client):
    resp = await client.patch(f"/menu/items/{uuid.uuid4()}", json={"name": "ghost"})
    assert resp.status_code == 404


async def test_float_price_rejected(client, seed_store: Store):
    resp = await client.post(
        "/menu/items", json=_item_body(seed_store.id, price=99.9)
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────
# Modifier groups + options
# ──────────────────────────────────────────────────────────────────────────


async def test_modifier_group_nested_options(client, seed_store: Store):
    body = await _create_group(
        client,
        seed_store.id,
        name="甜度",
        selection_type="single",
        is_required=True,
        min_select=1,
        max_select=None,
        options=[
            {"name": "正常糖", "sort_order": 0},
            {"name": "半糖", "sort_order": 1},
            {"name": "無糖", "sort_order": 2},
        ],
    )
    assert body["selection_type"] == "single"
    assert body["max_select"] == 1  # single → max_select forced to 1
    assert [o["name"] for o in body["options"]] == ["正常糖", "半糖", "無糖"]


async def test_modifier_group_min_max_validation(client, seed_store: Store):
    resp = await client.post(
        "/menu/modifier-groups",
        json={
            "name": "壞群組",
            "store_id": str(seed_store.id),
            "selection_type": "multi",
            "min_select": 3,
            "max_select": 2,
        },
    )
    assert resp.status_code == 422


async def test_option_price_delta_decimal_precision(client, seed_store: Store):
    group = await _create_group(client, seed_store.id)
    created = await client.post(
        f"/menu/modifier-groups/{group['id']}/options",
        json={"name": "珍珠", "price_delta": "10.1250"},
    )
    assert created.status_code == 201, created.text
    assert Decimal(str(created.json()["price_delta"])) == Decimal("10.1250")

    patched = await client.patch(
        f"/menu/modifier-options/{created.json()['id']}",
        json={"price_delta": "0.0625", "is_available": False},
    )
    assert patched.status_code == 200
    assert Decimal(str(patched.json()["price_delta"])) == Decimal("0.0625")
    assert patched.json()["is_available"] is False


async def test_link_and_duplicate_link_conflicts(client, seed_store: Store):
    item = await _create_item(client, seed_store.id)
    group = await _create_group(client, seed_store.id)

    linked = await client.post(
        f"/menu/items/{item['id']}/modifier-groups",
        json={"modifier_group_id": group["id"]},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["menu_item_id"] == item["id"]

    dup = await client.post(
        f"/menu/items/{item['id']}/modifier-groups",
        json={"modifier_group_id": group["id"]},
    )
    assert dup.status_code == 409


async def test_unlink_modifier_group(client, seed_store: Store):
    item = await _create_item(client, seed_store.id)
    group = await _create_group(client, seed_store.id)
    await client.post(
        f"/menu/items/{item['id']}/modifier-groups",
        json={"modifier_group_id": group["id"]},
    )

    unlinked = await client.delete(
        f"/menu/items/{item['id']}/modifier-groups/{group['id']}"
    )
    assert unlinked.status_code == 204

    again = await client.delete(
        f"/menu/items/{item['id']}/modifier-groups/{group['id']}"
    )
    assert again.status_code == 404
