"""LINE messenger contract + Phase-1 stub + Phase-2 HTTP implementation skeleton.

Use sites (Phase 2):
- Reservation: confirm booking, send T-1h reminder, request reply for re-confirm
- Queue: 您前面剩 2 組, 預估 8 分鐘 (ASCII comma to dodge RUF002)
- Food safety: incident notification to affected customers from the
  lot_no traceability query (see docs/08 §1.5)
- Marketing: tier-based segment push (gold tier birthday gift)
- Employee: shift swap requests / clock-in reminders

Test strategy:
- Phase 1: business code injects ``StubLineMessenger``; tests assert
  ``stub.sent_messages == [...]`` instead of patching the SDK.
- Phase 2: integration test against LINE sandbox channel.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("restaurant_api.integrations.line")


# Narrow message kind for v1; LINE's full Flex Message API stays internal
# to the HTTP backend.
MessageKind = Literal["text", "template", "flex"]


@dataclass(frozen=True)
class LineMessage:
    """A single outbound LINE message.

    ``kind="text"`` covers 90% of Phase-1 needs (booking confirm, queue
    call, OTP). ``kind="template"`` and ``"flex"`` are forward-compat
    for marketing pushes; ``payload`` carries the LINE-shaped JSON.
    """

    kind: MessageKind
    text: str  # always populated; for template/flex this is the alt-text
    payload: dict[str, object] | None = None  # required when kind != "text"


@dataclass(frozen=True)
class BroadcastAudience:
    """Segment selector for ``broadcast``.

    Phase 1: tier-based only (REGULAR / SILVER / GOLD / PLATINUM).
    Phase 2: arbitrary tag set + recency / spend predicates.
    """

    tiers: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    # Optional explicit recipient list — bypasses the segment query.
    explicit_user_ids: tuple[str, ...] = ()

    def is_targeted(self) -> bool:
        return bool(self.tiers or self.tags or self.explicit_user_ids)


# ──────────────────────────────────────────────────────────────────────────
# Abstract contract
# ──────────────────────────────────────────────────────────────────────────


class LineMessenger(ABC):
    """The narrow LINE surface every restaurant_api caller depends on."""

    @abstractmethod
    async def push(self, line_user_id: str, message: LineMessage) -> None:
        """1-to-1 push to a known line_user_id."""

    @abstractmethod
    async def broadcast(self, audience: BroadcastAudience, message: LineMessage) -> int:
        """Send to a tier/tag-defined segment. Returns the count delivered."""

    @abstractmethod
    async def reply(self, reply_token: str, message: LineMessage) -> None:
        """Reply within the LINE webhook 60-second reply window."""


# ──────────────────────────────────────────────────────────────────────────
# Phase-1 stub — no network, fully deterministic for tests
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class StubLineMessenger(LineMessenger):
    """In-memory implementation used in Phase 1 and in all tests.

    Records every sent message so tests can assert on it. Logs at INFO so
    the dev process surface still shows what *would* be sent.
    """

    sent_messages: list[dict[str, object]] = field(default_factory=list)

    async def push(self, line_user_id: str, message: LineMessage) -> None:
        entry = {"op": "push", "to": line_user_id, "message": message}
        self.sent_messages.append(entry)
        logger.info("LINE push (stub) → %s: %s", line_user_id, message.text[:80])

    async def broadcast(self, audience: BroadcastAudience, message: LineMessage) -> int:
        entry = {"op": "broadcast", "audience": audience, "message": message}
        self.sent_messages.append(entry)
        delivered = len(audience.explicit_user_ids) or 0
        logger.info(
            "LINE broadcast (stub) → tiers=%s tags=%s (delivered=%d): %s",
            audience.tiers,
            audience.tags,
            delivered,
            message.text[:80],
        )
        return delivered

    async def reply(self, reply_token: str, message: LineMessage) -> None:
        entry = {"op": "reply", "reply_token": reply_token, "message": message}
        self.sent_messages.append(entry)
        logger.info("LINE reply (stub) ← token=%s: %s", reply_token[:8], message.text[:80])


# ──────────────────────────────────────────────────────────────────────────
# Phase-2 HTTP implementation skeleton — DO NOT USE IN PROD WITHOUT WIRING
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class HttpLineMessenger(LineMessenger):
    """Real LINE Messaging API back-end.

    Phase-2 deliverable. Skeleton here so the production wiring lives in
    one obvious place when we switch.

    Required env: ``LINE_CHANNEL_ACCESS_TOKEN``, ``LINE_CHANNEL_SECRET``.
    """

    channel_access_token: str
    channel_secret: str
    base_url: str = "https://api.line.me/v2/bot"

    @classmethod
    def from_env(cls) -> HttpLineMessenger:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        secret = os.environ.get("LINE_CHANNEL_SECRET", "")
        if not token or not secret:
            raise RuntimeError(
                "LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET must be set"
            )
        return cls(channel_access_token=token, channel_secret=secret)

    async def push(self, line_user_id: str, message: LineMessage) -> None:
        # Phase 2 implementation: httpx.AsyncClient.post(f"{base_url}/message/push", ...)
        raise NotImplementedError("HttpLineMessenger.push wired up in Phase 2")

    async def broadcast(self, audience: BroadcastAudience, message: LineMessage) -> int:
        # Phase 2: query customers by tier/tag, then push in batches of 500.
        raise NotImplementedError("HttpLineMessenger.broadcast wired up in Phase 2")

    async def reply(self, reply_token: str, message: LineMessage) -> None:
        # Phase 2: httpx.AsyncClient.post(f"{base_url}/message/reply", ...)
        raise NotImplementedError("HttpLineMessenger.reply wired up in Phase 2")


# ──────────────────────────────────────────────────────────────────────────
# DI helper
# ──────────────────────────────────────────────────────────────────────────


_singleton: LineMessenger | None = None


def get_messenger() -> LineMessenger:
    """Return the process-wide messenger.

    Phase 1: ``StubLineMessenger`` (records, no network).
    Phase 2: switch to ``HttpLineMessenger.from_env()`` once
    LINE_CHANNEL_ACCESS_TOKEN is populated in the deploy env.
    """
    global _singleton
    if _singleton is None:
        if os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"):
            _singleton = HttpLineMessenger.from_env()
        else:
            _singleton = StubLineMessenger()
    return _singleton


def reset_messenger() -> None:
    """For tests — clear the singleton."""
    global _singleton
    _singleton = None
