"""K8s-style health endpoint tests — /health/live + /health/ready + composite."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from restaurant_api.api import health as health_module
from restaurant_api.main import app


async def test_live_returns_200_when_running():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alive"
    assert body["service"] == "restaurant_api"
    assert body["version"]


async def test_live_returns_503_during_shutdown():
    """Once shutdown begins, /health/live flips to 503 so LB drains."""
    # Snapshot the original flag so other tests aren't affected.
    original = health_module._SHUTTING_DOWN
    health_module.mark_shutting_down()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health/live")
        assert r.status_code == 503
        assert r.json()["status"] == "draining"
    finally:
        health_module._SHUTTING_DOWN = original


async def test_ready_200_when_db_reachable():
    with patch(
        "restaurant_api.api.health.ping_db",
        AsyncMock(return_value={"ok": True, "version": "PostgreSQL 16", "database": "resto"}),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True


async def test_ready_503_when_db_down():
    with patch(
        "restaurant_api.api.health.ping_db",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["ok"] is False
    assert "connection refused" in body["checks"]["database"]["error"]


async def test_legacy_health_routes_to_ready():
    """The bare /health endpoint is kept as a backward-compat alias for /health/ready."""
    with patch(
        "restaurant_api.api.health.ping_db",
        AsyncMock(return_value={"ok": True, "version": "PostgreSQL 16", "database": "resto"}),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
    assert r.status_code == 200
    body = r.json()
    # composite uses the ready-style payload
    assert body["status"] == "ready"
    assert "database" in body["checks"]
