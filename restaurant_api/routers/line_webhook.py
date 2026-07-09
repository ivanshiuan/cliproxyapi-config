"""``/line/webhook`` — inbound LINE events (public, signature-verified).

The one setup step with no management API is the follow/welcome message — LINE
only lets you set the static "greeting message" in the console. But a webhook
that catches the ``follow`` event and *pushes* a welcome message is the
API-driven equivalent, and richer (Flex, not plain text). Point your channel's
Webhook URL at ``<base>/line/webhook`` and turn "Use webhook" on; the static
greeting can then stay empty.

Public by design (LINE's servers call it, not an authenticated admin) — the
trust comes from the ``X-Line-Signature`` HMAC check, not a session cookie.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Header, Request, Response

from ..api.deps import Messenger
from ..config import get_settings
from ..integrations.line import LineMessage, parse_events, verify_signature

logger = logging.getLogger("restaurant_api.line_webhook")

router = APIRouter(prefix="/line", tags=["line-webhook"])

_WELCOME_ASSET = (
    Path(__file__).resolve().parent.parent
    / "line_assets"
    / "flex_welcome_launch.final.json"
)


@lru_cache(maxsize=1)
def _welcome_message() -> LineMessage:
    """Load the committed welcome Flex bubble as a LineMessage (cached).

    Falls back to a plain-text welcome if the asset is missing or malformed,
    so a follow event always gets *some* greeting rather than nothing.
    """
    fallback = LineMessage(kind="text", text="🔥 歡迎加入周霸虎老火鍋！開幕輪盤天天抽，快來試手氣 🎡")
    try:
        data = json.loads(_WELCOME_ASSET.read_text(encoding="utf-8"))
        msg = data["messages"][0]
        if msg.get("type") == "flex":
            return LineMessage(
                kind="flex",
                text=msg.get("altText", "歡迎加入周霸虎老火鍋"),
                payload=msg["contents"],
            )
        if msg.get("type") == "text":
            return LineMessage(kind="text", text=msg["text"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        logger.warning("line_webhook.welcome_asset_unloadable", extra={"path": str(_WELCOME_ASSET)})
    return fallback


@router.post("/webhook", include_in_schema=False)
async def line_webhook(
    request: Request,
    messenger: Messenger,
    x_line_signature: str = Header(default=""),
) -> Response:
    """Verify the signature, then push a welcome Flex on each ``follow`` event.

    Always returns 200 once the signature is valid — LINE retries on non-2xx,
    and a per-event push failure must not trigger a redelivery storm, so those
    are logged and swallowed. A bad/missing signature is 403 (reject spoofed
    calls); an unconfigured channel secret is 503 (misconfiguration, not spoof).
    """
    body = await request.body()
    secret = get_settings().line_channel_secret
    if not secret:
        logger.warning("line_webhook.no_channel_secret")
        return Response(status_code=503)
    if not verify_signature(secret, body, x_line_signature):
        return Response(status_code=403)

    for event in parse_events(body):
        if event.type == "follow" and event.source_user_id:
            try:
                await messenger.push(event.source_user_id, _welcome_message())
            except Exception as e:  # never let one push wedge the 200
                logger.warning(
                    "line_webhook.welcome_push_failed",
                    extra={"error": f"{type(e).__name__}: {e}"},
                )
    return Response(status_code=200)


__all__ = ["router"]
