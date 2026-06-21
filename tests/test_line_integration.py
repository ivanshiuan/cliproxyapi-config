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
    LineApiError,
    LineMessage,
    LineMessenger,
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
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None)
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
