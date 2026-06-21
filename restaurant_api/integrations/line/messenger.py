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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

import httpx

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
# Message serialization + transport error
# ──────────────────────────────────────────────────────────────────────────


def line_message_payload(message: LineMessage) -> dict[str, object]:
    """Serialize a ``LineMessage`` into a LINE Messaging API message object.

    ``text`` → ``{"type": "text", ...}``; ``flex``/``template`` wrap the
    caller-supplied ``payload`` and use ``text`` as the required alt-text.
    """
    if message.kind == "text":
        return {"type": "text", "text": message.text}
    if message.kind == "flex":
        return {
            "type": "flex",
            "altText": message.text,
            "contents": message.payload or {},
        }
    if message.kind == "template":
        return {
            "type": "template",
            "altText": message.text,
            "template": message.payload or {},
        }
    raise ValueError(f"unsupported LINE message kind: {message.kind!r}")


class LineApiError(RuntimeError):
    """Raised when the LINE Messaging API returns a non-2xx response.

    Carries the status code and (truncated) body so the best-effort push
    wrappers at call sites log something actionable.
    """

    def __init__(self, status: int, body: str, path: str) -> None:
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"LINE API {path} failed: HTTP {status}: {body[:300]}")


# ──────────────────────────────────────────────────────────────────────────
# Phase-2 HTTP implementation — real LINE Messaging API
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class HttpLineMessenger(LineMessenger):
    """Real LINE Messaging API back-end (api.line.me/v2/bot).

    Outbound push/reply/multicast authenticate with the channel access
    token alone; ``channel_secret`` is only needed by the (separate)
    inbound webhook signature check, so it is optional here.

    A single ``httpx.AsyncClient`` is created lazily and reused for the
    process lifetime; ``transport`` lets tests inject ``httpx.MockTransport``.
    """

    channel_access_token: str
    channel_secret: str = ""
    base_url: str = "https://api.line.me/v2/bot"
    timeout_seconds: float = 10.0
    transport: httpx.BaseTransport | None = None
    _client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> HttpLineMessenger:
        """Build from Settings (which reads both the OS env and ``.env``)."""
        from ...config import get_settings

        settings = get_settings()
        token = settings.line_channel_access_token
        if not token:
            raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN must be set")
        return cls(
            channel_access_token=token,
            channel_secret=settings.line_channel_secret,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                headers={"Authorization": f"Bearer {self.channel_access_token}"},
                transport=self.transport,  # type: ignore[arg-type]
            )
        return self._client

    async def aclose(self) -> None:
        """Close the underlying client (call on app shutdown / in tests)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, body: dict[str, object]) -> None:
        resp = await self._get_client().post(path, json=body)
        if resp.status_code >= 400:
            raise LineApiError(resp.status_code, resp.text, path)

    async def push(self, line_user_id: str, message: LineMessage) -> None:
        await self._post(
            "/message/push",
            {"to": line_user_id, "messages": [line_message_payload(message)]},
        )

    async def reply(self, reply_token: str, message: LineMessage) -> None:
        await self._post(
            "/message/reply",
            {"replyToken": reply_token, "messages": [line_message_payload(message)]},
        )

    async def broadcast(self, audience: BroadcastAudience, message: LineMessage) -> int:
        """Multicast to ``audience.explicit_user_ids`` in batches of 500.

        Tier/tag → user_id resolution is a DB concern owned by the caller
        (e.g. the membership-lifecycle job already resolves recipients and
        calls ``push`` per member), so a tier/tag-only audience is rejected
        here rather than silently dropped.
        """
        recipients = list(audience.explicit_user_ids)
        if not recipients:
            raise NotImplementedError(
                "HttpLineMessenger.broadcast requires explicit_user_ids; "
                "resolve tiers/tags to user ids in the caller before broadcasting"
            )
        payload = line_message_payload(message)
        delivered = 0
        for start in range(0, len(recipients), 500):
            batch = recipients[start : start + 500]
            await self._post("/message/multicast", {"to": batch, "messages": [payload]})
            delivered += len(batch)
        return delivered


# ──────────────────────────────────────────────────────────────────────────
# DI helper
# ──────────────────────────────────────────────────────────────────────────


_singleton: LineMessenger | None = None


def get_messenger() -> LineMessenger:
    """Return the process-wide messenger.

    Returns the real ``HttpLineMessenger`` when ``LINE_CHANNEL_ACCESS_TOKEN``
    is configured (via OS env or ``.env``), otherwise the in-memory
    ``StubLineMessenger`` used in dev and tests.
    """
    global _singleton
    if _singleton is None:
        from ...config import get_settings

        if get_settings().line_channel_access_token:
            _singleton = HttpLineMessenger.from_env()
        else:
            _singleton = StubLineMessenger()
    return _singleton


def reset_messenger() -> None:
    """For tests — clear the singleton."""
    global _singleton
    _singleton = None
