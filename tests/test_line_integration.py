"""Tests for the LINE integration abstraction."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import httpx
import pytest

from restaurant_api.config import get_settings
from restaurant_api.integrations.line import (
    BroadcastAudience,
    HttpLineMessenger,
    LiffAdminClient,
    LiffApiError,
    LineApiError,
    LineMessage,
    LineMessenger,
    RichMenuAdminClient,
    RichMenuApiError,
    StubLineMessenger,
    get_messenger,
    line_message_payload,
)
from restaurant_api.integrations.line.messenger import reset_messenger


def test_stub_messenger_records_push():
    stub = StubLineMessenger()
    msg = LineMessage(kind="text", text="您的訂位已確認")

    import asyncio

    asyncio.run(stub.push("U123abc", msg))

    assert len(stub.sent_messages) == 1
    entry = stub.sent_messages[0]
    assert entry["op"] == "push"
    assert entry["to"] == "U123abc"
    assert entry["message"] is msg


def test_stub_messenger_records_broadcast_with_explicit_recipients():
    stub = StubLineMessenger()
    audience = BroadcastAudience(
        tiers=("gold",), explicit_user_ids=("U1", "U2", "U3")
    )
    msg = LineMessage(kind="text", text="Birthday gift")

    import asyncio

    delivered = asyncio.run(stub.broadcast(audience, msg))

    assert delivered == 3
    assert stub.sent_messages[0]["op"] == "broadcast"


def test_stub_messenger_records_reply():
    stub = StubLineMessenger()
    msg = LineMessage(kind="text", text="收到")

    import asyncio

    asyncio.run(stub.reply("R-TOKEN-abc", msg))

    assert stub.sent_messages[0]["op"] == "reply"
    assert stub.sent_messages[0]["reply_token"] == "R-TOKEN-abc"


def test_audience_is_targeted_detection():
    assert not BroadcastAudience().is_targeted()
    assert BroadcastAudience(tiers=("gold",)).is_targeted()
    assert BroadcastAudience(tags=("birthday-week",)).is_targeted()
    assert BroadcastAudience(explicit_user_ids=("U1",)).is_targeted()


def test_line_message_is_frozen():
    from dataclasses import FrozenInstanceError

    msg = LineMessage(kind="text", text="x")
    with pytest.raises(FrozenInstanceError):
        msg.text = "y"  # type: ignore[misc]


def test_get_messenger_returns_stub_when_no_env():
    reset_messenger()
    get_settings.cache_clear()
    # ``get_messenger`` only reads ``.line_channel_access_token``; stub it empty so
    # the assertion holds regardless of any real LINE creds in the local env/.env
    # (which otherwise leak in via os.environ AND the dotenv file).
    from types import SimpleNamespace

    no_line = SimpleNamespace(line_channel_access_token="")
    with patch("restaurant_api.config.get_settings", return_value=no_line):
        m = get_messenger()
        assert isinstance(m, StubLineMessenger)
    reset_messenger()
    get_settings.cache_clear()


def test_get_messenger_returns_http_when_env_set():
    reset_messenger()
    get_settings.cache_clear()
    with patch.dict(os.environ, {"LINE_CHANNEL_ACCESS_TOKEN": "test-token"}):
        m = get_messenger()
        assert isinstance(m, HttpLineMessenger)
        assert m.channel_access_token == "test-token"
    reset_messenger()
    get_settings.cache_clear()


# ── HttpLineMessenger: real transport behaviour (mocked) ───────────────────


def _mock_messenger(handler) -> HttpLineMessenger:
    """An HttpLineMessenger whose HTTP calls hit an in-memory MockTransport."""
    return HttpLineMessenger(
        channel_access_token="test-token",
        transport=httpx.MockTransport(handler),
    )


async def _push_and_close(messenger: HttpLineMessenger, *args) -> None:
    try:
        await messenger.push(*args)
    finally:
        await messenger.aclose()


def test_http_push_posts_text_with_bearer_auth():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    asyncio.run(
        _push_and_close(
            _mock_messenger(handler), "U1", LineMessage(kind="text", text="中獎通知")
        )
    )

    assert captured["url"].endswith("/message/push")  # type: ignore[union-attr]
    assert captured["auth"] == "Bearer test-token"
    assert captured["body"] == {
        "to": "U1",
        "messages": [{"type": "text", "text": "中獎通知"}],
    }


def test_http_push_raises_line_api_error_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"message":"Authentication failed"}')

    with pytest.raises(LineApiError) as excinfo:
        asyncio.run(
            _push_and_close(
                _mock_messenger(handler), "U1", LineMessage(kind="text", text="x")
            )
        )
    assert excinfo.value.status == 401
    assert "Authentication failed" in excinfo.value.body


def test_http_push_wraps_connection_failure_as_line_api_error():
    """A network-level failure (proxy block, DNS, timeout — never got an HTTP
    response at all) must not escape as a raw httpx exception; best-effort
    push wrappers at call sites only ever catch LineApiError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LineApiError) as excinfo:
        asyncio.run(
            _push_and_close(
                _mock_messenger(handler), "U1", LineMessage(kind="text", text="x")
            )
        )
    assert excinfo.value.status == 0
    assert "ConnectError" in excinfo.value.body


def test_http_broadcast_multicasts_in_batches_of_500():
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/message/multicast")
        batch_sizes.append(len(json.loads(request.content)["to"]))
        return httpx.Response(200, json={})

    messenger = _mock_messenger(handler)
    audience = BroadcastAudience(explicit_user_ids=tuple(f"U{i}" for i in range(1100)))

    async def _run() -> int:
        try:
            return await messenger.broadcast(
                audience, LineMessage(kind="text", text="開幕快訊")
            )
        finally:
            await messenger.aclose()

    delivered = asyncio.run(_run())
    assert delivered == 1100
    assert batch_sizes == [500, 500, 100]


def test_http_broadcast_rejects_unresolved_segment():
    """Tier/tag audiences must be resolved to user ids by the caller."""
    messenger = HttpLineMessenger(channel_access_token="t")
    with pytest.raises(NotImplementedError):
        asyncio.run(
            messenger.broadcast(
                BroadcastAudience(tiers=("gold",)),
                LineMessage(kind="text", text="x"),
            )
        )


def test_line_message_payload_serializes_each_kind():
    assert line_message_payload(LineMessage(kind="text", text="hi")) == {
        "type": "text",
        "text": "hi",
    }
    assert line_message_payload(
        LineMessage(kind="flex", text="alt", payload={"a": 1})
    ) == {"type": "flex", "altText": "alt", "contents": {"a": 1}}
    assert line_message_payload(
        LineMessage(kind="template", text="alt", payload={"b": 2})
    ) == {"type": "template", "altText": "alt", "template": {"b": 2}}


def test_line_messenger_is_abstract():
    """Concrete classes must implement push/broadcast/reply."""
    with pytest.raises(TypeError):
        LineMessenger()  # type: ignore[abstract]


# ── LiffAdminClient: real transport behaviour (mocked) ──────────────────────


def _mock_liff_client(handler) -> LiffAdminClient:
    return LiffAdminClient(
        channel_access_token="test-token",
        transport=httpx.MockTransport(handler),
    )


async def _ensure_and_close(client: LiffAdminClient, *args, **kwargs):
    try:
        return await client.ensure_app(*args, **kwargs)
    finally:
        await client.aclose()


def test_liff_list_apps_sends_bearer_auth():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"apps": [{"liffId": "L1", "view": {"url": "https://x/a"}}]})

    async def _run():
        client = _mock_liff_client(handler)
        try:
            return await client.list_apps()
        finally:
            await client.aclose()

    apps = asyncio.run(_run())
    assert captured["url"].endswith("/apps")  # type: ignore[union-attr]
    assert captured["auth"] == "Bearer test-token"
    assert apps == [{"liffId": "L1", "view": {"url": "https://x/a"}}]


def test_liff_create_app_posts_view_and_returns_id():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"liffId": "1234567890-AbCdEfGh"})

    async def _run():
        client = _mock_liff_client(handler)
        try:
            return await client.create_app(
                view_url="https://chouhutiger.onrender.com/demo/campaign/grand-open",
                description="開幕輪盤-grand-open",
            )
        finally:
            await client.aclose()

    liff_id = asyncio.run(_run())
    assert liff_id == "1234567890-AbCdEfGh"
    body = captured["body"]
    assert body["view"] == {  # type: ignore[index]
        "type": "full",
        "url": "https://chouhutiger.onrender.com/demo/campaign/grand-open",
    }
    assert body["description"] == "開幕輪盤-grand-open"  # type: ignore[index]


def test_liff_create_app_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"message":"invalid view url"}')

    async def _run():
        client = _mock_liff_client(handler)
        try:
            await client.create_app(view_url="not-a-url")
        finally:
            await client.aclose()

    with pytest.raises(LiffApiError) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status == 400
    assert "invalid view url" in excinfo.value.body


def test_liff_list_apps_wraps_connection_failure():
    """Same regression guard as HttpLineMessenger: a proxy block / DNS
    failure / timeout must come back as LiffApiError, not a raw httpx
    exception bubbling up as an unhandled 500 at the router."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("403 Forbidden")

    async def _run():
        client = _mock_liff_client(handler)
        try:
            await client.list_apps()
        finally:
            await client.aclose()

    with pytest.raises(LiffApiError) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status == 0
    assert "ProxyError" in excinfo.value.body


def test_liff_ensure_app_reuses_existing_matching_view_url():
    """Idempotent: a matching view.url on an existing app is reused, not
    duplicated — re-running the setup call after the first success is a no-op."""
    target_url = "https://chouhutiger.onrender.com/demo/campaign/grand-open"
    create_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls
        if request.url.path.endswith("/apps") and request.method == "GET":
            return httpx.Response(
                200,
                json={"apps": [{"liffId": "EXISTING-ID", "view": {"url": target_url}}]},
            )
        create_calls += 1
        return httpx.Response(200, json={"liffId": "SHOULD-NOT-BE-CALLED"})

    liff_id, created = asyncio.run(
        _ensure_and_close(_mock_liff_client(handler), view_url=target_url)
    )
    assert liff_id == "EXISTING-ID"
    assert created is False
    assert create_calls == 0


def test_liff_ensure_app_creates_when_no_match():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"apps": []})
        return httpx.Response(200, json={"liffId": "NEW-ID"})

    liff_id, created = asyncio.run(
        _ensure_and_close(
            _mock_liff_client(handler),
            view_url="https://chouhutiger.onrender.com/demo/campaign/grand-open",
        )
    )
    assert liff_id == "NEW-ID"
    assert created is True


# ── RichMenuAdminClient: real transport behaviour (mocked) ──────────────────


def _mock_richmenu_client(handler) -> RichMenuAdminClient:
    return RichMenuAdminClient(
        channel_access_token="test-token",
        transport=httpx.MockTransport(handler),
    )


_RICHMENU_BODY = {
    "size": {"width": 2500, "height": 843},
    "selected": True,
    "name": "周霸虎-開幕上線版-v1",
    "chatBarText": "🎡 開幕輪盤抽獎",
    "areas": [],
}


def test_richmenu_create_posts_body_and_returns_id():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"richMenuId": "RM-1"})

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            return await client.create_richmenu(_RICHMENU_BODY)
        finally:
            await client.aclose()

    richmenu_id = asyncio.run(_run())
    assert richmenu_id == "RM-1"
    assert captured["host"] == "api.line.me"
    assert captured["body"] == _RICHMENU_BODY


def test_richmenu_upload_image_posts_bytes_to_data_host():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = request.content
        return httpx.Response(200, json={})

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            await client.upload_image("RM-1", b"\x89PNG-fake-bytes")
        finally:
            await client.aclose()

    asyncio.run(_run())
    assert captured["host"] == "api-data.line.me"
    assert captured["content_type"] == "image/png"
    assert captured["body"] == b"\x89PNG-fake-bytes"


def test_richmenu_set_default_posts_to_user_all():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={})

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            await client.set_default("RM-1")
        finally:
            await client.aclose()

    asyncio.run(_run())
    assert captured["path"] == "/v2/bot/user/all/richmenu/RM-1"


def test_richmenu_create_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"message":"invalid richmenu"}')

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            await client.create_richmenu(_RICHMENU_BODY)
        finally:
            await client.aclose()

    with pytest.raises(RichMenuApiError) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status == 400
    assert "invalid richmenu" in excinfo.value.body


def test_richmenu_wraps_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            await client.list_richmenus()
        finally:
            await client.aclose()

    with pytest.raises(RichMenuApiError) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status == 0
    assert "ConnectTimeout" in excinfo.value.body


def test_richmenu_ensure_creates_fresh_when_no_name_match():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v2/bot/richmenu/list":
            return httpx.Response(200, json={"richmenus": []})
        if request.url.path == "/v2/bot/richmenu":
            return httpx.Response(200, json={"richMenuId": "RM-NEW"})
        return httpx.Response(200, json={})

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            return await client.ensure_richmenu(_RICHMENU_BODY, b"png-bytes")
        finally:
            await client.aclose()

    richmenu_id, replaced = asyncio.run(_run())
    assert richmenu_id == "RM-NEW"
    assert replaced is False
    assert "DELETE /v2/bot/richmenu/RM-NEW" not in calls
    assert "POST /v2/bot/richmenu/RM-NEW/content" in calls
    assert "POST /v2/bot/user/all/richmenu/RM-NEW" in calls


def test_richmenu_ensure_deletes_existing_same_name_first():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v2/bot/richmenu/list":
            return httpx.Response(
                200,
                json={
                    "richmenus": [
                        {"richMenuId": "RM-OLD", "name": "周霸虎-開幕上線版-v1"}
                    ]
                },
            )
        if request.url.path == "/v2/bot/richmenu":
            return httpx.Response(200, json={"richMenuId": "RM-NEW"})
        return httpx.Response(200, json={})

    async def _run():
        client = _mock_richmenu_client(handler)
        try:
            return await client.ensure_richmenu(_RICHMENU_BODY, b"png-bytes")
        finally:
            await client.aclose()

    richmenu_id, replaced = asyncio.run(_run())
    assert richmenu_id == "RM-NEW"
    assert replaced is True
    assert "DELETE /v2/bot/richmenu/RM-OLD" in calls
