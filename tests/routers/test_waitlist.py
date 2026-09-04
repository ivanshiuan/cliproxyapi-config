"""P9.2 — 遠端線上候位 (docs/23 G6): 自動配號 + 公開安全候位看板."""

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


async def _join(client: httpx.AsyncClient, store: str, name: str, party: int = 2) -> dict:
    r = await client.post(
        "/queue", json={"store_id": store, "contact_name": name, "party_size": party}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_server_assigns_sequential_queue_no(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _join(client, store, "甲")
    b = await _join(client, store, "乙")
    assert a["queue_no"] == "W001"
    assert b["queue_no"] == "W002"


async def test_explicit_queue_no_still_wins(client: httpx.AsyncClient, seed_store: Store) -> None:
    store = str(seed_store.id)
    r = await client.post(
        "/queue",
        json={"store_id": store, "contact_name": "丙", "party_size": 4, "queue_no": "A99"},
    )
    assert r.status_code == 201 and r.json()["queue_no"] == "A99"


async def test_board_is_public_safe_and_ordered(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _join(client, store, "王小明", 2)
    await _join(client, store, "李大華", 4)

    board = (await client.get("/queue/board", params={"store_id": store})).json()
    assert board["waiting_count"] == 2
    assert [e["queue_no"] for e in board["entries"]] == [a["queue_no"], "W002"]
    for e in board["entries"]:
        assert "contact_name" not in e and "contact_phone" not in e and "id" not in e

    # 叫號後看板狀態變 called, 仍留在看板上
    call = await client.patch(f"/queue/{a['id']}/status", json={"status": "called"})
    assert call.status_code == 200, call.text
    board2 = (await client.get("/queue/board", params={"store_id": store})).json()
    called = next(e for e in board2["entries"] if e["queue_no"] == a["queue_no"])
    assert called["status"] == "called"
    assert board2["waiting_count"] == 1


async def test_seated_and_abandoned_drop_off_board(
    client: httpx.AsyncClient, seed_store: Store
) -> None:
    store = str(seed_store.id)
    a = await _join(client, store, "被帶位")
    b = await _join(client, store, "自行取消")

    await client.patch(f"/queue/{a['id']}/status", json={"status": "called"})
    await client.patch(f"/queue/{a['id']}/status", json={"status": "seated"})
    cancel = await client.patch(
        f"/queue/{b['id']}/status", json={"status": "abandoned", "reason": "顧客線上自行取消"}
    )
    assert cancel.status_code == 200, cancel.text

    board = (await client.get("/queue/board", params={"store_id": store})).json()
    nos = [e["queue_no"] for e in board["entries"]]
    assert a["queue_no"] not in nos and b["queue_no"] not in nos
    assert board["waiting_count"] == 0
