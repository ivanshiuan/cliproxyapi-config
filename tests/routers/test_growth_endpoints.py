"""HTTP-level integration tests for the growth endpoints.

Service logic is covered elsewhere; THIS file drives the actual mounted routers
through ``httpx.AsyncClient`` to catch wiring bugs service tests can't: response
model serialization (Decimal / enum), path+query binding, status codes, and
dependency injection (tenant + messenger overrides).

All DB writes ride the per-test SAVEPOINT and roll back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db, get_line_messenger
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.main import app
from restaurant_api.models import Customer, Order, OrderStatus, Store, Tenant

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def stub_messenger() -> StubLineMessenger:
    return StubLineMessenger()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    stub_messenger: StubLineMessenger,
) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = lambda: seed_tenant.id
    app.dependency_overrides[get_line_messenger] = lambda: stub_messenger
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _customer(
    session: AsyncSession, tenant_id: uuid.UUID, **kw
) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="客", **kw)
    session.add(cust)
    await session.flush()
    return cust


# ── 儲值 (stored value) ───────────────────────────────────────────────────────


async def test_http_stored_value_topup_balance_spend_flow(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)

    # top-up 1000 → +200 bonus → 1200
    r = await client.post(
        f"/customers/{cust.id}/stored-value/top-up", json={"amount": "1000"}
    )
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["stored_value_balance"])) == Decimal("1200")

    # balance reads back 1200
    r = await client.get(f"/customers/{cust.id}/stored-value")
    assert r.status_code == 200
    assert Decimal(str(r.json()["stored_value_balance"])) == Decimal("1200")

    # spend 450 → 750
    r = await client.post(
        f"/customers/{cust.id}/stored-value/spend", json={"amount": "450"}
    )
    assert r.status_code == 201
    assert Decimal(str(r.json()["stored_value_balance"])) == Decimal("750")

    # overspend → 409
    r = await client.post(
        f"/customers/{cust.id}/stored-value/spend", json={"amount": "99999"}
    )
    assert r.status_code == 409

    # ledger lists rows (topup + bonus + spend = 3)
    r = await client.get(f"/customers/{cust.id}/stored-value/ledger")
    assert r.status_code == 200
    assert len(r.json()) == 3


async def test_http_stored_value_rejects_float_amount(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    # JSON float must be rejected at the money boundary (422).
    r = await client.post(
        f"/customers/{cust.id}/stored-value/top-up", json={"amount": 1000.5}
    )
    assert r.status_code == 422


# ── 裂變 (referral) ────────────────────────────────────────────────────────────


async def test_http_referral_code_minted(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)
    r = await client.get(f"/customers/{cust.id}/referral-code")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["customer_id"] == str(cust.id)
    assert len(body["referral_code"]) == 8


# ── UGC ───────────────────────────────────────────────────────────────────────


async def test_http_ugc_submit_queue_approve(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    cust = await _customer(db_session, seed_tenant.id)

    r = await client.post(
        "/ugc/submissions",
        json={"customer_id": str(cust.id), "kind": "google_review"},
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    assert r.json()["status"] == "pending"

    # queue filter
    r = await client.get("/ugc/submissions", params={"ugc_status": "pending"})
    assert r.status_code == 200
    assert any(s["id"] == sub_id for s in r.json())

    # approve → google review = 100 pts
    r = await client.post(f"/ugc/submissions/{sub_id}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert Decimal(str(r.json()["reward_points"])) == Decimal("100")

    # double-approve → 409
    r = await client.post(f"/ugc/submissions/{sub_id}/approve", json={})
    assert r.status_code == 409


# ── 成效報表 + RFM ─────────────────────────────────────────────────────────────


async def test_http_membership_stats_and_window(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/membership/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_days"] is None
    for key in ("total_members", "tiers", "points", "stored_value", "referral", "ugc"):
        assert key in body

    r = await client.get("/membership/stats", params={"days": 7})
    assert r.status_code == 200
    assert r.json()["window_days"] == 7

    # out-of-range window rejected
    r = await client.get("/membership/stats", params={"days": 9999})
    assert r.status_code == 422


async def test_http_segments_and_broadcast(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    stub_messenger: StubLineMessenger,
) -> None:
    # one AT_RISK member with LINE: 5 orders ~60d ago, lifetime 5000
    m = await _customer(
        db_session, seed_tenant.id,
        lifetime_value=Decimal("5000"), line_user_id=f"U{uuid.uuid4().hex[:16]}",
    )
    today = date.today()
    for k in range(5):
        db_session.add(
            Order(
                tenant_id=seed_tenant.id, store_id=seed_store.id, customer_id=m.id,
                order_no=f"H{uuid.uuid4().hex[:10]}",
                business_date=today - timedelta(days=60 + k),
                opened_at=datetime.now(UTC), closed_at=datetime.now(UTC),
                status=OrderStatus.CLOSED,
            )
        )
    await db_session.flush()

    r = await client.get("/membership/segments")
    assert r.status_code == 200, r.text
    segs = {s["segment"]: s["count"] for s in r.json()["segments"]}
    assert len(segs) == 6  # all labels present
    assert segs["at_risk"] >= 1

    # broadcast to at_risk → delivers to the 1 LINE-reachable member
    r = await client.post(
        "/membership/segments/at_risk/broadcast",
        json={"message": "想念您 回來享 9 折"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segment"] == "at_risk"
    assert body["audience_size"] >= 1
    assert body["delivered"] == body["audience_size"]

    # unknown segment in path → 422 (enum validation)
    r = await client.post(
        "/membership/segments/not_a_segment/broadcast", json={"message": "x"}
    )
    assert r.status_code == 422
