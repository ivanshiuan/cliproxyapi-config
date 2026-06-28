"""Unit tests for restaurant_api.api.deps RBAC additions.

Tests get_current_user / require_role / require_any_role / require_permission
in isolation by mounting a tiny test app that exercises each dep, so we
don't need the full restaurant_api wiring to be RBAC-ready.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from fastapi import Depends, FastAPI

from restaurant_api.api.deps import (
    get_current_user,
    require_any_role,
    require_permission,
    require_role,
)
from restaurant_api.api.errors import DomainError, domain_error_handler
from restaurant_api.config import get_settings
from restaurant_api.security.jwt import encode_access_token

# Factory calls hoisted to module level — B008 (function call in default
# argument) would otherwise flag the inner factory, and the dep machinery
# also slightly prefers reusing the same callable identity.
_REQ_ROLE_MARKETING = require_role("marketing")
_REQ_ANY_ROLE = require_any_role(["marketing", "store_manager"])
_REQ_PERM_ORDERS_CREATE = require_permission("orders:create")


def _build_app() -> FastAPI:
    """Tiny app that mounts each dep on its own route."""
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]

    @app.get("/me")
    async def me(user=Depends(get_current_user)):
        return {"user_id": str(user.employee_id) if user else None}

    @app.get("/needs-role-marketing")
    async def needs_role(user=Depends(_REQ_ROLE_MARKETING)):
        return {"user_id": str(user.employee_id)}

    @app.get("/needs-any-role")
    async def needs_any(user=Depends(_REQ_ANY_ROLE)):
        return {"user_id": str(user.employee_id)}

    @app.get("/needs-perm")
    async def needs_perm(user=Depends(_REQ_PERM_ORDERS_CREATE)):
        return {"user_id": str(user.employee_id)}

    return app


def _mint_token(
    *,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> str:
    token, _, _ = encode_access_token(
        employee_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        employee_role="staff",
        roles=roles or [],
        permissions=permissions or [],
    )
    return token


@pytest_asyncio.fixture
async def client():
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_get_current_user_returns_none_without_token_in_warn_mode(
    client, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "warn")
    r = await client.get("/me")
    assert r.status_code == 200
    assert r.json()["user_id"] is None


@pytest.mark.asyncio
async def test_get_current_user_401_without_token_in_enforce_mode(
    client, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "enforce")
    r = await client.get("/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_returns_payload_with_valid_token(client):
    token = _mint_token(roles=["marketing"], permissions=["orders:read"])
    r = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"]  # uuid present


@pytest.mark.asyncio
async def test_require_role_passes_when_role_present(client):
    token = _mint_token(roles=["marketing"])
    r = await client.get(
        "/needs-role-marketing",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_require_role_403_when_role_missing(client):
    token = _mint_token(roles=["staff"])
    r = await client.get(
        "/needs-role-marketing",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert "marketing" in body["error"]["message"]


@pytest.mark.asyncio
async def test_require_role_401_when_no_token(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "enforce")
    r = await client.get("/needs-role-marketing")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_require_any_role_passes_with_any_match(client):
    token = _mint_token(roles=["store_manager"])  # one of the allowed set
    r = await client.get(
        "/needs-any-role",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_require_any_role_403_with_no_match(client):
    token = _mint_token(roles=["supplier"])
    r = await client.get(
        "/needs-any-role",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_require_permission_passes(client):
    token = _mint_token(permissions=["orders:create", "orders:read"])
    r = await client.get(
        "/needs-perm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_require_permission_403_when_missing(client):
    token = _mint_token(permissions=["orders:read"])  # no orders:create
    r = await client.get(
        "/needs-perm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["details"]["required_permission"] == "orders:create"


@pytest.mark.asyncio
async def test_tampered_token_returns_401(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "enforce")
    token = _mint_token()
    # Mutate one char in the signature.
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    r = await client.get(
        "/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_malformed_authorization_header_treated_as_missing(
    client, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enforcement", "warn")
    # Missing "Bearer " prefix
    r = await client.get(
        "/me", headers={"Authorization": "just-a-token-no-prefix"}
    )
    # In warn mode this is treated like no token at all → user=None, 200.
    assert r.status_code == 200
    assert r.json()["user_id"] is None
