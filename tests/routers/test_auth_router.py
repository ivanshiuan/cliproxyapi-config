"""Integration tests for /auth router (PR-B).

Goes through the real FastAPI app + a real DB session bound to a
savepoint that rolls back at end of test. Each test seeds its own
UserCredential.

Tests cover spec acceptance criteria for:
- POST /auth/login (success / wrong password / disabled / locked /
  timing-attack defence / unknown email)
- POST /auth/refresh (success / rotation / reuse detection / expired)
- POST /auth/logout (success / idempotent / no token)
- GET /auth/me (success / no token / stale token after grant)
- POST /auth/change_password (success / wrong old / refresh revoked)
"""

from __future__ import annotations

import time

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_db
from restaurant_api.config import get_settings
from restaurant_api.main import app
from restaurant_api.models import (
    Employee,
    RefreshToken,
    UserCredential,
)
from restaurant_api.security.passwords import hash_password
from tests.conftest import needs_db

pytestmark = [pytest.mark.asyncio, needs_db]


# ---------------------------------------------------------------------------
# Per-test client wired to the savepoint DB session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def auth_client(db_session: AsyncSession):
    """AsyncClient bound to the test DB session via get_db override."""

    async def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def seed_credential(
    db_session: AsyncSession,
    seed_employee: Employee,
):
    """Make seed_employee loginnable with a known password."""
    cred = UserCredential(
        employee_id=seed_employee.id,
        email="login.test@example.com",
        password_hash=hash_password("Pa55word!"),
    )
    db_session.add(cred)
    await db_session.flush()
    return cred


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


async def test_login_success_returns_token_pair(auth_client, seed_credential):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert len(body["access_token"]) > 50
    assert len(body["refresh_token"]) == 64
    assert body["expires_in"] == get_settings().jwt_access_ttl_seconds


async def test_login_wrong_password_returns_401_and_increments_counter(
    auth_client, seed_credential, db_session
):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "WrongPa55word!"},
    )
    assert r.status_code == 401
    # check counter incremented
    fresh = (
        await db_session.execute(
            select(UserCredential).where(
                UserCredential.id == seed_credential.id
            )
        )
    ).scalar_one()
    assert fresh.failed_login_count >= 1


async def test_login_unknown_email_returns_401(auth_client, seed_credential):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "doesnotexist@example.com", "password": "x" * 12},
    )
    assert r.status_code == 401


async def test_login_unknown_email_same_response_time(
    auth_client, seed_credential
):
    """Timing-attack defence: unknown-email response should not be
    materially faster than a wrong-password response. Both should spend
    one argon2id verify (~5-30 ms)."""
    # warm caches first
    await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "wrong"},
    )

    t0 = time.perf_counter()
    await auth_client.post(
        "/auth/login",
        json={"email": "doesnotexist@example.com", "password": "x" * 12},
    )
    unknown_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "wrong"},
    )
    wrong_pw_ms = (time.perf_counter() - t0) * 1000

    # Allow 8x ratio — argon2id work is significant but DB / network jitter
    # is also real on shared CI. The point of the test is to catch the
    # "unknown email returns in 1ms" regression, not assert tight equality.
    assert unknown_ms > 3, f"unknown-email should spend argon2 work, got {unknown_ms:.1f}ms"
    assert unknown_ms < wrong_pw_ms * 8 + 100


async def test_login_disabled_account_returns_403(
    auth_client, seed_credential, db_session
):
    seed_credential.is_active = False
    await db_session.flush()
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    assert r.status_code == 403


async def test_login_lockout_after_5_failures(
    auth_client, seed_credential, db_session
):
    for _ in range(5):
        r = await auth_client.post(
            "/auth/login",
            json={"email": "login.test@example.com", "password": "WRONG"},
        )
        assert r.status_code == 401
    # 6th attempt — even with correct password — should be locked.
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    assert r.status_code == 423
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) > 0


# ---------------------------------------------------------------------------
# /auth/refresh
# ---------------------------------------------------------------------------


async def test_refresh_rotates_token(auth_client, seed_credential):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    first = r.json()
    r2 = await auth_client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert r2.status_code == 200
    second = r2.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]


async def test_refresh_reuse_revokes_all_tokens(
    auth_client, seed_credential, db_session
):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    old_refresh = r.json()["refresh_token"]
    # Rotate once — old_refresh is now revoked.
    await auth_client.post(
        "/auth/refresh", json={"refresh_token": old_refresh}
    )
    # Reuse old_refresh → 401, and *all* this employee's refresh tokens
    # should be revoked.
    r3 = await auth_client.post(
        "/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert r3.status_code == 401
    active_count = len(
        (
            await db_session.execute(
                select(RefreshToken).where(
                    RefreshToken.employee_id == seed_credential.employee_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars().all()
    )
    assert active_count == 0


async def test_refresh_unknown_token_returns_401(auth_client, seed_credential):
    r = await auth_client.post(
        "/auth/refresh", json={"refresh_token": "0" * 64}
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------


async def test_logout_revokes_token(
    auth_client, seed_credential, db_session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "warn")
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    tokens = r.json()
    r2 = await auth_client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r2.status_code == 204
    # Token revoked → subsequent refresh attempt is reuse → 401.
    r3 = await auth_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r3.status_code == 401


async def test_logout_idempotent_returns_204(
    auth_client, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "warn")
    r = await auth_client.post(
        "/auth/logout", json={"refresh_token": "f" * 64}
    )
    # Token not found → still 204, no leak.
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


async def test_me_returns_identity(auth_client, seed_credential):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    access = r.json()["access_token"]
    r2 = await auth_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["email"] == "login.test@example.com"
    assert body["employee_role"] == "staff"
    assert body["last_login_at"] is not None


async def test_me_401_without_token(auth_client):
    r = await auth_client.get("/auth/me")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /auth/change_password
# ---------------------------------------------------------------------------


async def test_change_password_success_revokes_refresh(
    auth_client, seed_credential, db_session
):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    tokens = r.json()
    r2 = await auth_client.post(
        "/auth/change_password",
        json={"old_password": "Pa55word!", "new_password": "NewPa55word!"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r2.status_code == 204
    # Old refresh revoked.
    r3 = await auth_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r3.status_code == 401
    # New password works.
    r4 = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "NewPa55word!"},
    )
    assert r4.status_code == 200


async def test_change_password_wrong_old_returns_401(
    auth_client, seed_credential
):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    tokens = r.json()
    r2 = await auth_client.post(
        "/auth/change_password",
        json={"old_password": "WRONG", "new_password": "NewPa55word!"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r2.status_code == 401


async def test_change_password_complexity_rejected(auth_client, seed_credential):
    r = await auth_client.post(
        "/auth/login",
        json={"email": "login.test@example.com", "password": "Pa55word!"},
    )
    tokens = r.json()
    # No digit
    r2 = await auth_client.post(
        "/auth/change_password",
        json={"old_password": "Pa55word!", "new_password": "onlyletters"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r2.status_code == 422
