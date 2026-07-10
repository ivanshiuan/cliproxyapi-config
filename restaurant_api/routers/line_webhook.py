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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Header, Request, Response

from ..api.deps import Messenger
from ..config import get_settings
from ..integrations.line import (
    LiffAdminClient,
    LiffApiError,
    LineMessage,
    parse_events,
    verify_signature,
)

logger = logging.getLogger("restaurant_api.line_webhook")

router = APIRouter(prefix="/line", tags=["line-webhook"])

_WELCOME_ASSET = (
    Path(__file__).resolve().parent.parent
    / "line_assets"
    / "flex_welcome_launch.final.json"
)

_DEFAULT_SLUG = "grand-open"


def _with_liff_param(uri: str, liff_id: str) -> str:
    """Sets (or replaces) the ``liff`` query param on a URL — authoritative
    over whatever the committed JSON asset happens to carry, so a stale
    baked-in id (e.g. from a one-off manual edit) can never shadow the
    current live LIFF app."""
    parsed = urlparse(uri)
    params = [(k, v) for k, v in parse_qsl(parsed.query) if k != "liff"]
    params.append(("liff", liff_id))
    return urlunparse(parsed._replace(query=urlencode(params)))


def _append_liff_param(node: object, liff_id: str) -> None:
    """Mutates any nested ``action.uri`` in-place to carry ``?liff=<id>``.

    Without this, the button opened from LINE's in-app browser has no way to
    identify the tapping user — the frontend silently falls back to an
    anonymous guest id (see static/index.html's ``LIFF_ID`` handling). The
    committed JSON asset can't bake this in since the LIFF id is only known
    once /admin/line/liff has actually registered the app.
    """
    if isinstance(node, dict):
        action = node.get("action")
        if isinstance(action, dict) and action.get("type") == "uri":
            uri = action.get("uri")
            if isinstance(uri, str):
                action["uri"] = _with_liff_param(uri, liff_id)
        for value in node.values():
            _append_liff_param(value, liff_id)
    elif isinstance(node, list):
        for item in node:
            _append_liff_param(item, liff_id)


@lru_cache(maxsize=1)
def _welcome_asset_text() -> str | None:
    """Cached raw file read — the JSON itself doesn't change per request;
    only the injected liff id (handled separately) does."""
    try:
        return _WELCOME_ASSET.read_text(encoding="utf-8")
    except OSError:
        logger.warning("line_webhook.welcome_asset_unloadable", extra={"path": str(_WELCOME_ASSET)})
        return None


def _welcome_message(liff_id: str | None) -> LineMessage:
    """Build the welcome Flex bubble from the committed asset, with the
    button URI made LIFF-aware when a liff_id is available.

    Falls back to a plain-text welcome if the asset is missing or malformed,
    so a follow event always gets *some* greeting rather than nothing.
    """
    fallback = LineMessage(kind="text", text="🔥 歡迎加入周霸虎老火鍋！開幕輪盤天天抽，快來試手氣 🎡")
    raw = _welcome_asset_text()
    if raw is None:
        return fallback
    try:
        data = json.loads(raw)
        msg = data["messages"][0]
        if msg.get("type") == "flex":
            contents = msg["contents"]
            if liff_id:
                _append_liff_param(contents, liff_id)
            return LineMessage(
                kind="flex",
                text=msg.get("altText", "歡迎加入周霸虎老火鍋"),
                payload=contents,
            )
        if msg.get("type") == "text":
            return LineMessage(kind="text", text=msg["text"])
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("line_webhook.welcome_asset_malformed", extra={"path": str(_WELCOME_ASSET)})
    return fallback


async def _resolve_liff_id(token: str, expected_view_url: str) -> str | None:
    """Best-effort lookup of the LIFF app registered for the wheel page —
    a lookup failure must never block the welcome push, just send it
    without auto-identity (customer falls back to manual/demo input)."""
    if not token:
        return None
    client = LiffAdminClient(channel_access_token=token)
    try:
        return await client.find_by_view_url(expected_view_url)
    except LiffApiError as e:
        logger.warning("line_webhook.liff_lookup_failed", extra={"error": str(e)})
        return None
    finally:
        await client.aclose()


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

    events = parse_events(body)
    if any(e.type == "follow" and e.source_user_id for e in events):
        settings = get_settings()
        root = str(request.base_url).rstrip("/")
        expected_view_url = f"{root}/demo/campaign/{_DEFAULT_SLUG}"
        liff_id = await _resolve_liff_id(settings.line_channel_access_token, expected_view_url)
        welcome = _welcome_message(liff_id)
        for event in events:
            if event.type == "follow" and event.source_user_id:
                try:
                    await messenger.push(event.source_user_id, welcome)
                except Exception as e:  # never let one push wedge the 200
                    logger.warning(
                        "line_webhook.welcome_push_failed",
                        extra={"error": f"{type(e).__name__}: {e}"},
                    )
    return Response(status_code=200)


__all__ = ["router"]
