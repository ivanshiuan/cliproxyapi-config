"""LINE Messaging integration — unified abstraction for顧客 + 員工 + 行銷 三軸.

Why this layer exists
---------------------
In a Taiwan F&B context, LINE is the universal pipe: reservation
confirmations, queue calls, food-safety incident alerts, marketing
push, employee shift broadcasts. Without a single abstraction, every
caller would directly import the LINE SDK and the day we switch
provider (or add Telegram for overseas tourists) is the day we touch
forty callsites.

The abstraction is intentionally narrow — three operations cover every
use case we've identified:

    push(user_id, message)         # 1-to-1 direct push
    broadcast(audience, message)   # tag-based segment broadcast
    reply(reply_token, message)    # webhook reply within 60s window

Concrete back-ends live in this package; ``StubLineMessenger`` is the
Phase-1 default and just logs. ``HttpLineMessenger`` (Phase 2) calls
the real LINE Messaging API.
"""

from __future__ import annotations

from .liff_admin import LiffAdminClient, LiffApiError
from .messenger import (
    BroadcastAudience,
    HttpLineMessenger,
    LineApiError,
    LineMessage,
    LineMessenger,
    StubLineMessenger,
    get_messenger,
    line_message_payload,
    reset_messenger,
)
from .richmenu_admin import RichMenuAdminClient, RichMenuApiError

__all__ = [
    "BroadcastAudience",
    "HttpLineMessenger",
    "LiffAdminClient",
    "LiffApiError",
    "LineApiError",
    "LineMessage",
    "LineMessenger",
    "RichMenuAdminClient",
    "RichMenuApiError",
    "StubLineMessenger",
    "get_messenger",
    "line_message_payload",
    "reset_messenger",
]
