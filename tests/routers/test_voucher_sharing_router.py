"""HTTP-level integration tests for the ``/vouchers`` router (模式 A / 模式 B).

Drives the mounted endpoints through ``httpx.AsyncClient`` to catch wiring bugs
service tests can't: response serialization (Decimal / enum), path+body binding,
status codes, and — critically — that the LINE messenger reaches the redeem
endpoint through DI (``get_line_messenger`` override), not a global singleton.

All DB writes ride the per-test SAVEPOINT and roll back.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.auth import AdminPrincipal, require_admin
from restaurant_api.api.deps import get_current_tenant_id, get_db, get_line_messenger
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.main import app
from restaurant_api.models import Customer, Tenant

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
    app.dependency_overrides[require_admin] = lambda: AdminPrincipal()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _customer(session: AsyncSession, tenant_id: uuid.UUID, **kw) -> Customer:
    cust = Customer(tenant_id=tenant_id, display_name="客", **kw)
    session.add(cust)
    await session.flush()
    return cust


# ── 模式 A end-to-end over HTTP ────────────────────────────────────────────────


async def test_gift_full_flow_http(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    seed_tenant: Tenant,
    stub_messenger: StubLineMessenger,
) -> None:
    sender = await _customer(
        db_session, seed_tenant.id, line_user_id=f"U{uuid.uuid4().hex[:16]}"
    )
    friend = await _customer(db_session, seed_tenant.id)

    # 1) member mints a gift link
    r = await client.post("/vouchers/gifts", json={"sender_id": str(sender.id)})
    assert r.status_code == 201, r.text
    gift = r.json()
    assert gift["status"] == "pending"
    token = gift["share_token"]

    # 2) friend claims it
    r = await client.post(
        f"/vouchers/gifts/{token}/claim", json={"recipient_id": str(friend.id)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "claimed"

    # 3) counter redeems → sender rewarded, LINE nudge via the DI'd stub
    r = await client.post(f"/vouchers/gifts/{gift['id']}/redeem")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "redeemed"
    assert body["rewarded_at"] is not None
    # the DI-injected stub caught the push — proves messenger wiring (not a global)
    assert any(m["op"] == "push" for m in stub_messenger.sent_messages)


async def test_cannot_claim_own_gift_http(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    r = await client.post("/vouchers/gifts", json={"sender_id": str(sender.id)})
    token = r.json()["share_token"]
    r = await client.post(
        f"/vouchers/gifts/{token}/claim", json={"recipient_id": str(sender.id)}
    )
    assert r.status_code == 409, r.text


async def test_redeem_unclaimed_gift_409_http(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    r = await client.post("/vouchers/gifts", json={"sender_id": str(sender.id)})
    gid = r.json()["id"]
    r = await client.post(f"/vouchers/gifts/{gid}/redeem")
    assert r.status_code == 409, r.text


async def test_create_gift_rejects_float_reward_http(
    client: httpx.AsyncClient, db_session: AsyncSession, seed_tenant: Tenant
) -> None:
    sender = await _customer(db_session, seed_tenant.id)
    # raw JSON float for a money field must be rejected at the boundary
    r = await client.post(
        "/vouchers/gifts",
        json={"sender_id": str(sender.id), "reward_amount": 10.5},
    )
    assert r.status_code == 422, r.text


# ── 模式 B over HTTP ───────────────────────────────────────────────────────────


async def test_grant_create_distribute_dashboard_http(
    client: httpx.AsyncClient, seed_tenant: Tenant
) -> None:
    valid_until = (datetime.now(UTC).date() + timedelta(days=60)).isoformat()
    r = await client.post(
        "/vouchers/grants",
        json={
            "grantee_name": "股東A",
            "voucher_kind": "cash_credit",
            "total_quota": 2,
            "valid_until": valid_until,
            "face_value": "200",
        },
    )
    assert r.status_code == 201, r.text
    grant = r.json()
    assert grant["used_count"] == 0 and grant["total_quota"] == 2

    # distribute twice → ok; third → 409 (hard ceiling)
    assert (await client.post(f"/vouchers/grants/{grant['id']}/distribute")).status_code == 200
    r2 = await client.post(f"/vouchers/grants/{grant['id']}/distribute")
    assert r2.status_code == 200 and r2.json()["used_count"] == 2
    r3 = await client.post(f"/vouchers/grants/{grant['id']}/distribute")
    assert r3.status_code == 409, r3.text

    # dashboard lists it
    r = await client.get("/vouchers/grants")
    assert r.status_code == 200
    assert any(g["id"] == grant["id"] for g in r.json())


async def test_grant_rejects_float_face_value_http(
    client: httpx.AsyncClient, seed_tenant: Tenant
) -> None:
    valid_until = (datetime.now(UTC).date() + timedelta(days=10)).isoformat()
    r = await client.post(
        "/vouchers/grants",
        json={
            "grantee_name": "x",
            "voucher_kind": "cash_credit",
            "total_quota": 1,
            "valid_until": valid_until,
            "face_value": 200.5,
        },
    )
    assert r.status_code == 422, r.text
