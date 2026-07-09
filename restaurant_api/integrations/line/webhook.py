"""LINE webhook inbound: signature verification + event parsing.

LINE POSTs to a single webhook URL for every inbound event (follow, message,
unfollow, …), signed with an ``X-Line-Signature`` HMAC-SHA256 header keyed by
the channel secret. This module does the security-critical bits — verify the
signature against the raw body, then parse the event envelope — leaving the
"what to do per event" policy to the router.

Kept separate from ``messenger.py`` (outbound) so the inbound trust boundary
is one small, individually-testable surface.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    """Constant-time check that ``signature`` is LINE's HMAC-SHA256 of ``body``.

    ``body`` MUST be the raw request bytes exactly as received — re-serializing
    parsed JSON can change whitespace/key order and break the digest.
    """
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class LineEvent:
    """One parsed webhook event — only the fields the app acts on.

    ``reply_token`` is present on replyable events (message/follow/…) and absent
    on others (unfollow); ``source_user_id`` is the LINE userId when the source
    is a 1:1 chat.
    """

    type: str
    source_user_id: str | None = None
    reply_token: str | None = None
    message_text: str | None = None


def parse_events(body: bytes) -> list[LineEvent]:
    """Parse the webhook envelope into ``LineEvent``s. Unknown fields ignored;
    a malformed body yields an empty list rather than raising (LINE retries on
    non-2xx, and a poison payload should not wedge the endpoint)."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return []
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw_events, list):
        return []

    events: list[LineEvent] = []
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        raw_source = ev.get("source")
        source: dict[str, object] = raw_source if isinstance(raw_source, dict) else {}
        raw_message = ev.get("message")
        message: dict[str, object] = raw_message if isinstance(raw_message, dict) else {}
        text = message.get("text") if message.get("type") == "text" else None
        events.append(
            LineEvent(
                type=str(ev.get("type", "")),
                source_user_id=source.get("userId"),  # type: ignore[arg-type]
                reply_token=ev.get("replyToken"),
                message_text=text,  # type: ignore[arg-type]
            )
        )
    return events


__all__ = ["LineEvent", "parse_events", "verify_signature"]
