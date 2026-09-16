"""P7.4 — 時段菜單 (menu item availability window)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import Store, Tenant
from restaurant_api.services.menu_service import is_item_available_now

_TPE = ZoneInfo("Asia/Taipei")


class _FakeItem:
    """Bare object with just the two attributes ``is_item_available_now`` reads."""

    def __init__(self, start, end):
        self.available_start_time = start
        self.available_end_time = end


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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _window_around(now, before_h: float, after_h: float):
    start = (now - timedelta(hours=before_h)).time()
    end = (now + timedelta(hours=after_h)).time()
    return start, end


def _window_excluding(now, back_h: float):
    """A narrow 1h window strictly `back_h` hours in the past — never covers now."""
    start = (now - timedelta(hours=back_h + 1)).time()
    end = (now - timedelta(hours=back_h)).time()
    return start, end


# ─── pure function: is_item_available_now ──────────────────────────────────


def test_no_window_always_available():
    item = _FakeItem(None, None)
    assert is_item_available_now(item, now=datetime.now(_TPE).time())


def test_normal_window_inside_and_outside():
    from datetime import time

    item = _FakeItem(time(11, 0), time(14, 0))
    assert is_item_available_now(item, now=time(12, 0))
    assert not is_item_available_now(item, now=time(15, 0))
    assert not is_item_available_now(item, now=time(10, 59))
    assert is_item_available_now(item, now=time(11, 0))  # start inclusive
    assert not is_item_available_now(item, now=time(14, 0))  # end exclusive


def test_overnight_wrap_window():
    from datetime import time

    item = _FakeItem(time(21, 0), time(2, 0))  # 宵夜
    assert is_item_available_now(item, now=time(23, 0))
    assert is_item_available_now(item, now=time(1, 0))
    assert not is_item_available_now(item, now=time(12, 0))


# ─── API: create/update validation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_one_side_of_window_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    r = await client.post(
        "/menu/items",
        json={
            "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "半夜限定", "price": "60",
            "available_start_time": "21:00:00",
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_zero_width_window_rejected(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    r = await client.post(
        "/menu/items",
        json={
            "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "壞品項", "price": "60",
            "available_start_time": "21:00:00", "available_end_time": "21:00:00",
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_valid_window_persists(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    r = await client.post(
        "/menu/items",
        json={
            "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "宵夜炒飯", "price": "80",
            "available_start_time": "21:00:00", "available_end_time": "02:00:00",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["available_start_time"] == "21:00:00"
    assert body["available_end_time"] == "02:00:00"


# ─── API: server-side enforcement on add-line ──────────────────────────────


@pytest.mark.asyncio
async def test_ordering_outside_window_rejected_409(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    now = datetime.now(_TPE)
    start, end = _window_excluding(now, back_h=3)
    item = (
        await client.post(
            "/menu/items",
            json={
                "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "早餐限定", "price": "50",
                "available_start_time": start.isoformat(), "available_end_time": end.isoformat(),
            },
        )
    ).json()
    t = (await client.post("/tables", json={"store_id": store, "name": f"AW-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item["id"], "qty": "1"}
    )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_ordering_inside_window_ok(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    now = datetime.now(_TPE)
    start, end = _window_around(now, before_h=1, after_h=1)
    item = (
        await client.post(
            "/menu/items",
            json={
                "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "現在供應", "price": "50",
                "available_start_time": start.isoformat(), "available_end_time": end.isoformat(),
            },
        )
    ).json()
    t = (await client.post("/tables", json={"store_id": store, "name": f"AW-{uuid.uuid4().hex[:4]}"})).json()
    sess = (await client.post(f"/tables/{t['id']}/open", json={"party_size": 2})).json()
    r = await client.post(
        f"/tables/sessions/{sess['id']}/lines", json={"menu_item_id": item["id"], "qty": "1"}
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_available_now_filter_excludes_out_of_window_items(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    now = datetime.now(_TPE)
    in_start, in_end = _window_around(now, before_h=1, after_h=1)
    out_start, out_end = _window_excluding(now, back_h=5)

    in_item = (
        await client.post(
            "/menu/items",
            json={
                "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "現在有賣", "price": "10",
                "available_start_time": in_start.isoformat(), "available_end_time": in_end.isoformat(),
            },
        )
    ).json()
    out_item = (
        await client.post(
            "/menu/items",
            json={
                "store_id": store, "sku": f"AW{uuid.uuid4().hex[:6]}", "name": "現在沒賣", "price": "10",
                "available_start_time": out_start.isoformat(), "available_end_time": out_end.isoformat(),
            },
        )
    ).json()

    rows = (
        await client.get("/menu/items", params={"store_id": store, "available_now": "true"})
    ).json()
    ids = {r["id"] for r in rows}
    assert in_item["id"] in ids
    assert out_item["id"] not in ids
