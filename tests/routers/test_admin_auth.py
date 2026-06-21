"""Tests for the admin-console auth gate (shared passcode → session cookie).

Covers the token primitives (sign/verify/expiry/tamper) and the HTTP surface:
login success/failure, the 401 gate on protected endpoints, that customer-
facing endpoints stay public, and logout.
"""

from __future__ import annotations

import uuid

import pytest

from restaurant_api.api.auth import (
    COOKIE_NAME,
    make_session_token,
    verify_session_token,
)
from restaurant_api.config import get_settings
from tests.conftest import needs_db

# Read the configured passcode rather than hard-coding the dev default, so the
# suite passes whether or not a local .env overrides RESTO_ADMIN_PASSCODE.
PASSCODE = get_settings().admin_passcode


# ── Token primitives (no DB / no HTTP) ──────────────────────────────────────


def test_token_roundtrip_valid() -> None:
    s = get_settings()
    token = make_session_token(s)
    assert verify_session_token(token, s) is True


def test_token_expired_is_invalid() -> None:
    s = get_settings()
    # Mint as if issued far in the past so exp < now.
    token = make_session_token(s, now=1000)
    assert verify_session_token(token, s, now=10_000_000_000) is False


def test_token_tampered_payload_is_invalid() -> None:
    s = get_settings()
    token = make_session_token(s)
    body, sig = token.split(".", 1)
    # Flip the signature → must fail constant-time compare.
    bad = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    assert verify_session_token(bad, s) is False


def test_token_garbage_is_invalid() -> None:
    s = get_settings()
    for junk in ("", "no-dot", "a.b.c", "!!!.???"):
        assert verify_session_token(junk, s) is False


# ── HTTP: login / session / logout ──────────────────────────────────────────


@needs_db
@pytest.mark.asyncio
async def test_login_wrong_passcode_401(anon_client) -> None:
    r = await anon_client.post("/admin/login", json={"passcode": "nope"})
    assert r.status_code == 401
    assert COOKIE_NAME not in r.cookies


@needs_db
@pytest.mark.asyncio
async def test_login_sets_cookie_and_session_ok(anon_client) -> None:
    # Before login: session probe is 401.
    assert (await anon_client.get("/admin/session")).status_code == 401

    r = await anon_client.post("/admin/login", json={"passcode": PASSCODE})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert COOKIE_NAME in r.cookies  # Set-Cookie issued

    # Cookie persists in the client jar → session now authenticated.
    me = await anon_client.get("/admin/session")
    assert me.status_code == 200
    assert me.json() == {"authenticated": True}

    # Logout clears it → 401 again.
    assert (await anon_client.post("/admin/logout")).status_code == 200
    assert (await anon_client.get("/admin/session")).status_code == 401


# ── HTTP: the gate on /campaigns endpoints ──────────────────────────────────


@needs_db
@pytest.mark.asyncio
async def test_management_endpoints_require_auth(anon_client) -> None:
    """Create/list campaign + counter redeem must 401 without a session."""
    assert (await anon_client.post("/campaigns", json={"name": "X", "slug": "x"})).status_code == 401
    assert (await anon_client.get("/campaigns")).status_code == 401
    rid = uuid.uuid4()
    assert (await anon_client.get(f"/campaigns/{rid}/vouchers/by-code/ABC")).status_code == 401
    assert (await anon_client.get(f"/campaigns/{rid}/stats")).status_code == 401
    assert (await anon_client.get(f"/campaigns/{rid}/vouchers.csv")).status_code == 401


@needs_db
@pytest.mark.asyncio
async def test_player_endpoints_are_public(anon_client) -> None:
    """Wheel / spin are customer-facing: no auth → they pass the gate and
    fail on the missing campaign (404), proving they are NOT 401-gated.
    """
    rid = uuid.uuid4()
    assert (await anon_client.get(f"/campaigns/{rid}/wheel")).status_code == 404
    spin = await anon_client.post(f"/campaigns/{rid}/spin", json={"phone": "0912000000"})
    assert spin.status_code == 404


@needs_db
@pytest.mark.asyncio
async def test_protected_endpoint_works_after_login(anon_client) -> None:
    """After login the cookie passes the gate: a management endpoint that
    returned 401 anonymously now returns 200. (Uses list, which needs no
    tenant row, so it's independent of seed/demo data.)
    """
    assert (await anon_client.get("/campaigns")).status_code == 401
    await anon_client.post("/admin/login", json={"passcode": PASSCODE})
    ok = await anon_client.get("/campaigns")
    assert ok.status_code == 200
    assert isinstance(ok.json(), list)
