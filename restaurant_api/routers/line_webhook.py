"""FastAPI router — ``/line/webhook`` (inbound LINE Messaging API events).

This is the receiving half of the LINE channel (the sending half lives in
``integrations.line.HttpLineMessenger``). LINE POSTs every user action —
messages, follows, postback button taps — to this endpoint.

Two things this layer is responsible for, and nothing more:

1. **Signature verification.** Every request carries ``X-Line-Signature``:
   the base64 HMAC-SHA256 of the raw body keyed by the channel secret.
   We verify it with a constant-time compare and reject anything that
   doesn't match — otherwise anyone who learns the URL could forge events.
2. **Parse + dispatch.** Valid events are parsed into typed models and
   handed to a *handler seam*. The default handler just logs. Business
   flows (reservation re-confirm on a postback, queue ack on a reply,
   food-safety opt-out) register their own handler via
   ``app.dependency_overrides[get_event_handler]`` once their semantics
   are pinned — so the secure intake ships now without guessing the flows.

DI seams (override in tests / wiring):
- ``get_line_channel_secret``  → the HMAC key.
- ``get_event_handler``        → ``(event, messenger) -> None`` coroutine.
- ``get_webhook_messenger``    → outbound messenger handlers may reply with.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import ValidationError

from ..api.deps import get_line_channel_secret, get_line_messenger
from ..api.errors import DomainError
from ..integrations.line import LineMessenger
from ..schemas.line import LineWebhookBody, LineWebhookEvent

logger = logging.getLogger("restaurant_api.integrations.line")

router = APIRouter(prefix="/line", tags=["line"])


# Coroutine that does something with one verified event. The messenger is
# passed in so a handler can reply/push without reaching for a global.
LineEventHandler = Callable[[LineWebhookEvent, LineMessenger], Awaitable[None]]


class InvalidSignatureError(DomainError):
    """X-Line-Signature missing or doesn't match — request is not from LINE."""

    code = "INVALID_LINE_SIGNATURE"
    status_code = status.HTTP_401_UNAUTHORIZED


class WebhookNotConfiguredError(DomainError):
    """No channel secret set — refuse rather than accept unverifiable events."""

    code = "LINE_WEBHOOK_NOT_CONFIGURED"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class InvalidWebhookPayloadError(DomainError):
    """Signature was valid but the JSON body didn't match the LINE schema."""

    code = "INVALID_LINE_WEBHOOK_PAYLOAD"
    status_code = status.HTTP_400_BAD_REQUEST


# ──────────────────────────────────────────────────────────────────────────
# DI seams
# ──────────────────────────────────────────────────────────────────────────
#
# The channel-secret and outbound-messenger providers live in ``api/deps.py``
# (the project's central DI module) and are imported above. The event-handler
# seam stays here because its default (`log_event`) is this router's concern;
# moving it to deps would create a router⇄deps import cycle.


async def log_event(event: LineWebhookEvent, messenger: LineMessenger) -> None:
    """Default handler — structured log, no side effects.

    Deliberately does not auto-reply: Phase-2 business flows replace this
    via dependency override once their semantics are decided.
    """
    logger.info(
        "line.webhook.event",
        extra={
            "line_event_type": event.type,
            "line_message_type": event.message.type if event.message else None,
            "has_reply_token": event.reply_token is not None,
            # Presence only — the raw LINE user id is PII and must not hit logs.
            "has_line_user_id": bool(event.source and event.source.user_id),
        },
    )


def get_event_handler() -> LineEventHandler:
    """The per-event handler. Default logs; override to wire business flows."""
    return log_event


SecretDep = Annotated[str, Depends(get_line_channel_secret)]
HandlerDep = Annotated[LineEventHandler, Depends(get_event_handler)]
MessengerDep = Annotated[LineMessenger, Depends(get_line_messenger)]


# ──────────────────────────────────────────────────────────────────────────
# Signature verification
# ──────────────────────────────────────────────────────────────────────────


def _expected_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    """Raise if the request can't be proven to come from LINE."""
    if not secret:
        raise WebhookNotConfiguredError(
            "LINE_CHANNEL_SECRET is not configured; webhook is disabled"
        )
    if not signature or not hmac.compare_digest(_expected_signature(secret, body), signature):
        raise InvalidSignatureError("X-Line-Signature verification failed")


# ──────────────────────────────────────────────────────────────────────────
# Endpoint
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="LINE Messaging API inbound webhook (signature-verified)",
)
async def line_webhook(
    request: Request,
    secret: SecretDep,
    handler: HandlerDep,
    messenger: MessengerDep,
    x_line_signature: Annotated[str | None, Header(alias="X-Line-Signature")] = None,
) -> dict[str, object]:
    # Raw bytes — the signature is over the exact body, so verify before parsing.
    body = await request.body()
    verify_signature(secret, body, x_line_signature)

    try:
        payload = LineWebhookBody.model_validate_json(body)
    except ValidationError as exc:
        # Signature proved the sender, but the body is malformed — answer with
        # the standard error envelope (400) instead of leaking a 500.
        raise InvalidWebhookPayloadError("Malformed LINE webhook payload") from exc
    for event in payload.events:
        await handler(event, messenger)

    return {"ok": True, "handled": len(payload.events)}


__all__ = [
    "InvalidSignatureError",
    "InvalidWebhookPayloadError",
    "WebhookNotConfiguredError",
    "get_event_handler",
    "log_event",
    "router",
    "verify_signature",
]
