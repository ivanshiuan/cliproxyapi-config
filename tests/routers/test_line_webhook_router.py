"""Integration tests for POST /line/webhook — signature gate + follow→welcome push."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]

from restaurant_api.api.deps import get_line_messenger
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.main import app
from restaurant_api.routers import line_webhook

pytestmark = pytest.mark.asyncio

_SECRET = "test-channel-secret"


def _sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


@pytest_asyncio.fixture
async def stub_messenger() -> StubLineMessenger:
    return StubLineMessenger()


@pytest_asyncio.fixture
async def client(
    stub_messenger: StubLineMessenger, monkeypatch
) -> AsyncIterator[httpx.AsyncClient]:
    # Channel secret comes from settings inside the router; stub it to a known value.
    monkeypatch.setattr(
        line_webhook,
        "get_settings",
        lambda: SimpleNamespace(line_channel_secret=_SECRET),
    )
    app.dependency_overrides[get_line_messenger] = lambda: stub_messenger
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_webhook_rejects_bad_signature(client: httpx.AsyncClient) -> None:
    body = json.dumps({"events": []}).encode()
    resp = await client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": "wrong"}
    )
    assert resp.status_code == 403


async def test_webhook_503_when_secret_unconfigured(
    stub_messenger: StubLineMessenger, monkeypatch
) -> None:
    monkeypatch.setattr(
        line_webhook, "get_settings", lambda: SimpleNamespace(line_channel_secret="")
    )
    app.dependency_overrides[get_line_messenger] = lambda: stub_messenger
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            body = json.dumps({"events": []}).encode()
            resp = await ac.post(
                "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
            )
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()


async def test_webhook_follow_pushes_welcome(
    client: httpx.AsyncClient, stub_messenger: StubLineMessenger
) -> None:
    body = json.dumps(
        {"events": [{"type": "follow", "source": {"userId": "U-NEW-FRIEND"}}]}
    ).encode()
    resp = await client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
    )
    assert resp.status_code == 200
    assert len(stub_messenger.sent_messages) == 1
    entry = stub_messenger.sent_messages[0]
    assert entry["op"] == "push"
    assert entry["to"] == "U-NEW-FRIEND"
    # The committed welcome asset is a Flex bubble.
    assert entry["message"].kind == "flex"  # type: ignore[union-attr]


async def test_webhook_ignores_non_follow_events(
    client: httpx.AsyncClient, stub_messenger: StubLineMessenger
) -> None:
    body = json.dumps(
        {
            "events": [
                {"type": "unfollow", "source": {"userId": "U1"}},
                {"type": "message", "source": {"userId": "U1"}, "message": {"type": "text", "text": "hi"}},
            ]
        }
    ).encode()
    resp = await client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
    )
    assert resp.status_code == 200
    assert stub_messenger.sent_messages == []


async def test_webhook_push_failure_still_returns_200(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    """A push error must not surface as non-2xx (LINE would retry-storm)."""

    async def _boom(*_a, **_k):
        raise RuntimeError("LINE down")

    class BrokenMessenger(StubLineMessenger):
        push = _boom

    app.dependency_overrides[get_line_messenger] = lambda: BrokenMessenger()
    body = json.dumps(
        {"events": [{"type": "follow", "source": {"userId": "U1"}}]}
    ).encode()
    resp = await client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
    )
    assert resp.status_code == 200
