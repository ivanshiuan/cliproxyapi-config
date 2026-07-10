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


# ── _with_liff_param / _append_liff_param: pure unit tests ──────────────────


def test_with_liff_param_adds_when_absent():
    uri = "https://x.example/demo/campaign/grand-open"
    assert line_webhook._with_liff_param(uri, "NEW-ID") == uri + "?liff=NEW-ID"


def test_with_liff_param_replaces_stale_value_authoritatively():
    """Guards the exact bug a committed-file staleness would cause: an old
    baked-in ?liff= must never shadow the current live LIFF id."""
    uri = "https://x.example/demo/campaign/grand-open?liff=OLD-STALE-ID"
    assert line_webhook._with_liff_param(uri, "CURRENT-ID") == (
        "https://x.example/demo/campaign/grand-open?liff=CURRENT-ID"
    )


def test_with_liff_param_preserves_other_query_params():
    uri = "https://x.example/demo/?campaign=abc&brand=%E5%91%A8%E9%9C%B8%E8%99%8E"
    result = line_webhook._with_liff_param(uri, "ID1")
    assert "campaign=abc" in result
    assert "liff=ID1" in result


def test_append_liff_param_walks_nested_flex_structure():
    contents = {
        "type": "bubble",
        "footer": {
            "contents": [
                {"action": {"type": "uri", "uri": "https://x.example/a"}},
                {"action": {"type": "message", "text": "no uri here"}},
            ]
        },
    }
    line_webhook._append_liff_param(contents, "ID2")
    assert contents["footer"]["contents"][0]["action"]["uri"] == "https://x.example/a?liff=ID2"
    assert contents["footer"]["contents"][1]["action"] == {"type": "message", "text": "no uri here"}


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
    # Channel secret comes from settings inside the router; stub it to a known
    # value. No access token by default -> _resolve_liff_id short-circuits to
    # None without a network call, matching the pre-LIFF-lookup test baseline.
    monkeypatch.setattr(
        line_webhook,
        "get_settings",
        lambda: SimpleNamespace(line_channel_secret=_SECRET, line_channel_access_token=""),
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


async def test_webhook_follow_embeds_liff_param_when_app_registered(
    stub_messenger: StubLineMessenger, monkeypatch
) -> None:
    """The bug this guards: without the liff query param, the welcome
    button's LIFF SDK init never runs, and the wheel silently falls back to
    anonymous guest play instead of the tapper's real LINE identity."""

    class _FakeLiffClient:
        def __init__(self, channel_access_token: str) -> None:
            pass

        async def find_by_view_url(self, view_url: str) -> str | None:
            assert view_url == "http://test/demo/campaign/grand-open"
            return "WEBHOOK-LIFF-ID"

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        line_webhook,
        "get_settings",
        lambda: SimpleNamespace(line_channel_secret=_SECRET, line_channel_access_token="tok"),
    )
    monkeypatch.setattr(line_webhook, "LiffAdminClient", _FakeLiffClient)
    app.dependency_overrides[get_line_messenger] = lambda: stub_messenger

    body = json.dumps(
        {"events": [{"type": "follow", "source": {"userId": "U-NEW-FRIEND"}}]}
    ).encode()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
            )
        assert resp.status_code == 200
        entry = stub_messenger.sent_messages[0]
        button = entry["message"].payload["footer"]["contents"][0]  # type: ignore[union-attr,index]
        assert button["action"]["uri"].endswith("?liff=WEBHOOK-LIFF-ID")
    finally:
        app.dependency_overrides.clear()


async def test_webhook_follow_lookup_failure_still_sends_welcome(
    client: httpx.AsyncClient, stub_messenger: StubLineMessenger, monkeypatch
) -> None:
    """A LIFF lookup failure must not block the welcome push — just send it
    without auto-identity, same fail-open posture as the push call itself."""
    from restaurant_api.integrations.line import LiffApiError

    class _BrokenLiffClient:
        def __init__(self, channel_access_token: str) -> None:
            pass

        async def find_by_view_url(self, view_url: str) -> str | None:
            raise LiffApiError(500, "boom", "/apps")

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        line_webhook,
        "get_settings",
        lambda: SimpleNamespace(line_channel_secret=_SECRET, line_channel_access_token="tok"),
    )
    monkeypatch.setattr(line_webhook, "LiffAdminClient", _BrokenLiffClient)

    body = json.dumps(
        {"events": [{"type": "follow", "source": {"userId": "U1"}}]}
    ).encode()
    resp = await client.post(
        "/line/webhook", content=body, headers={"X-Line-Signature": _sign(body)}
    )
    assert resp.status_code == 200
    assert len(stub_messenger.sent_messages) == 1


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
