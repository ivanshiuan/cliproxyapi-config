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


def test_http_messenger_phase2_methods_unimplemented():
    """Phase-1 guarantee: calling HttpLineMessenger raises clearly, doesn't fail silently."""
    m = HttpLineMessenger(channel_access_token="t", channel_secret="s")
    import asyncio

    with pytest.raises(NotImplementedError):
        asyncio.run(m.push("U1", LineMessage(kind="text", text="x")))


def test_line_messenger_is_abstract():
    """Concrete classes must implement push/broadcast/reply."""
    with pytest.raises(TypeError):
        LineMessenger()  # type: ignore[abstract]
