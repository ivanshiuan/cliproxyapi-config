"""Tests for the LINE integration abstraction."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from restaurant_api.integrations.line import (
    BroadcastAudience,
    HttpLineMessenger,
    LineMessage,
    LineMessenger,
    StubLineMessenger,
    get_messenger,
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
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None)
        m = get_messenger()
        assert isinstance(m, StubLineMessenger)
    reset_messenger()


def test_get_messenger_returns_http_when_env_set():
    reset_messenger()
    with patch.dict(
        os.environ,
        {
            "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
            "LINE_CHANNEL_SECRET": "test-secret",
        },
    ):
        m = get_messenger()
        assert isinstance(m, HttpLineMessenger)
    reset_messenger()


def test_line_messenger_is_abstract():
    """Concrete classes must implement push/broadcast/reply."""
    with pytest.raises(TypeError):
        LineMessenger()  # type: ignore[abstract]


# ──────────────────────────────────────────────────────────────────────────
# HttpLineMessenger — exercise the real request-building path against an
# httpx.MockTransport so we assert URL / auth / body shape with no network
# and no live LINE credentials.
# ──────────────────────────────────────────────────────────────────────────


def _messenger_recording(responder):
    """Build an HttpLineMessenger whose transport records every request.

    ``responder`` maps a request to the httpx.Response to return. The
    captured requests are returned alongside so tests can assert on them.
    """
    import httpx

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return responder(request)

    messenger = HttpLineMessenger(
        channel_access_token="test-token",
        channel_secret="test-secret",
        transport=httpx.MockTransport(handler),
    )
    return messenger, captured


def test_http_push_hits_push_endpoint_with_auth_and_text_body():
    import asyncio
    import json

    import httpx

    messenger, captured = _messenger_recording(lambda req: httpx.Response(200, json={}))
    asyncio.run(messenger.push("U123", LineMessage(kind="text", text="您的訂位已確認")))

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.line.me/v2/bot/message/push"
    assert req.headers["Authorization"] == "Bearer test-token"
    body = json.loads(req.content)
    assert body == {"to": "U123", "messages": [{"type": "text", "text": "您的訂位已確認"}]}


def test_http_reply_hits_reply_endpoint_with_reply_token():
    import asyncio
    import json

    import httpx

    messenger, captured = _messenger_recording(lambda req: httpx.Response(200, json={}))
    asyncio.run(messenger.reply("R-TOKEN", LineMessage(kind="text", text="收到")))

    req = captured[0]
    assert str(req.url) == "https://api.line.me/v2/bot/message/reply"
    body = json.loads(req.content)
    assert body["replyToken"] == "R-TOKEN"
    assert body["messages"] == [{"type": "text", "text": "收到"}]


def test_http_template_message_serializes_alt_text_and_payload():
    import asyncio
    import json

    import httpx

    messenger, captured = _messenger_recording(lambda req: httpx.Response(200, json={}))
    msg = LineMessage(
        kind="template",
        text="alt text here",
        payload={"template": {"type": "buttons", "text": "pick"}},
    )
    asyncio.run(messenger.push("U1", msg))

    sent = json.loads(captured[0].content)["messages"][0]
    assert sent == {
        "type": "template",
        "altText": "alt text here",
        "template": {"type": "buttons", "text": "pick"},
    }


def test_http_broadcast_multicasts_in_batches_of_500():
    import asyncio
    import json

    import httpx

    messenger, captured = _messenger_recording(lambda req: httpx.Response(200, json={}))
    user_ids = tuple(f"U{i}" for i in range(501))
    audience = BroadcastAudience(explicit_user_ids=user_ids)

    delivered = asyncio.run(messenger.broadcast(audience, LineMessage(kind="text", text="hi")))

    assert delivered == 501
    assert len(captured) == 2  # 500 + 1
    for req in captured:
        assert str(req.url) == "https://api.line.me/v2/bot/message/multicast"
    first = json.loads(captured[0].content)
    second = json.loads(captured[1].content)
    assert len(first["to"]) == 500
    assert len(second["to"]) == 1
    assert second["to"] == ["U500"]


def test_http_broadcast_requires_explicit_ids_for_segment_audience():
    """Tier/tag-only audiences must be resolved to user ids upstream (DB-free adapter)."""
    import asyncio

    messenger, captured = _messenger_recording(lambda req: None)  # never called
    audience = BroadcastAudience(tiers=("gold",))

    with pytest.raises(ValueError, match="explicit_user_ids"):
        asyncio.run(messenger.broadcast(audience, LineMessage(kind="text", text="x")))
    assert captured == []


def test_http_broadcast_empty_audience_sends_nothing():
    import asyncio

    messenger, captured = _messenger_recording(lambda req: None)
    delivered = asyncio.run(messenger.broadcast(BroadcastAudience(), LineMessage(kind="text", text="x")))

    assert delivered == 0
    assert captured == []


def test_http_non_2xx_raises_line_api_error():
    import asyncio

    import httpx

    from restaurant_api.integrations.line import LineApiError

    messenger, _ = _messenger_recording(
        lambda req: httpx.Response(403, json={"message": "Invalid token"})
    )

    with pytest.raises(LineApiError) as exc:
        asyncio.run(messenger.push("U1", LineMessage(kind="text", text="x")))
    assert exc.value.status_code == 403
    assert exc.value.path == "/message/push"
