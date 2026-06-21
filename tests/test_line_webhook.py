"""Tests for the inbound LINE webhook router.

Self-contained: mounts only the webhook router on a tiny app (no DB), so
these run without the Postgres-backed ``client`` fixture. Signatures are
computed exactly the way LINE does, so we exercise the real verification
path rather than mocking it away.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI

from restaurant_api.api.errors import DomainError, domain_error_handler
from restaurant_api.integrations.line import StubLineMessenger
from restaurant_api.routers.line_webhook import (
    InvalidSignatureError,
    WebhookNotConfiguredError,
    get_event_handler,
    get_line_channel_secret,
    get_webhook_messenger,
    router,
    verify_signature,
)

SECRET = "test-channel-secret"


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")


def _build_app(*, secret: str = SECRET, handler=None):
    """Tiny app with the webhook router + captured events list."""
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(router)

    captured: list = []

    async def _capture(event, messenger) -> None:
        captured.append(event)

    app.dependency_overrides[get_line_channel_secret] = lambda: secret
    app.dependency_overrides[get_webhook_messenger] = lambda: StubLineMessenger()
    app.dependency_overrides[get_event_handler] = lambda: (handler or _capture)
    return app, captured


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ──────────────────────────────────────────────────────────────────────────
# Signature verification — unit level
# ──────────────────────────────────────────────────────────────────────────


def test_verify_signature_accepts_matching():
    body = b'{"events":[]}'
    verify_signature(SECRET, body, _sign(SECRET, body))  # no raise


def test_verify_signature_rejects_tampered_body():
    body = b'{"events":[]}'
    sig = _sign(SECRET, body)
    with pytest.raises(InvalidSignatureError):
        verify_signature(SECRET, b'{"events":[{"type":"x"}]}', sig)


def test_verify_signature_rejects_missing():
    with pytest.raises(InvalidSignatureError):
        verify_signature(SECRET, b"{}", None)


def test_verify_signature_unconfigured_secret():
    with pytest.raises(WebhookNotConfiguredError):
        verify_signature("", b"{}", "anything")


# ──────────────────────────────────────────────────────────────────────────
# Endpoint — full request path
# ──────────────────────────────────────────────────────────────────────────


async def test_webhook_accepts_valid_signature_and_dispatches():
    app, captured = _build_app()
    payload = {
        "destination": "Udest",
        "events": [
            {
                "type": "message",
                "replyToken": "R-1",
                "timestamp": 1700000000000,
                "source": {"type": "user", "userId": "U-abc"},
                "message": {"type": "text", "id": "m1", "text": "明天訂位"},
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")

    async with _client(app) as c:
        resp = await c.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": _sign(SECRET, body)}
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "handled": 1}
    assert len(captured) == 1
    event = captured[0]
    assert event.type == "message"
    assert event.reply_token == "R-1"
    assert event.source.user_id == "U-abc"
    assert event.message.text == "明天訂位"


async def test_webhook_rejects_bad_signature_without_dispatching():
    app, captured = _build_app()
    body = json.dumps({"events": [{"type": "message"}]}).encode("utf-8")

    async with _client(app) as c:
        resp = await c.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": "deadbeef"}
        )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_LINE_SIGNATURE"
    assert captured == []  # forged events never reach the handler


async def test_webhook_missing_signature_header_rejected():
    app, captured = _build_app()
    body = b'{"events":[]}'
    async with _client(app) as c:
        resp = await c.post("/line/webhook", content=body)
    assert resp.status_code == 401
    assert captured == []


async def test_webhook_unconfigured_secret_returns_503():
    app, _ = _build_app(secret="")
    body = b'{"events":[]}'
    async with _client(app) as c:
        resp = await c.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": _sign("x", body)}
        )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "LINE_WEBHOOK_NOT_CONFIGURED"


async def test_webhook_parses_postback_and_ignores_unknown_fields():
    app, captured = _build_app()
    payload = {
        "events": [
            {
                "type": "postback",
                "replyToken": "R-2",
                "postback": {"data": "action=reconfirm&id=42", "extraneous": "ok"},
                "futureField": {"nested": 1},  # LINE adds fields → must not 500
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    async with _client(app) as c:
        resp = await c.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": _sign(SECRET, body)}
        )

    assert resp.status_code == 200
    assert captured[0].type == "postback"
    assert captured[0].postback.data == "action=reconfirm&id=42"


async def test_webhook_empty_events_handled_zero():
    app, captured = _build_app()
    body = b'{"events":[]}'
    async with _client(app) as c:
        resp = await c.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": _sign(SECRET, body)}
        )
    assert resp.status_code == 200
    assert resp.json()["handled"] == 0
    assert captured == []
