"""Pydantic models for inbound LINE webhook events.

LINE delivers a JSON body ``{"destination": "...", "events": [...]}`` where
each event is camelCase. We parse only the fields Phase-2 flows need and
``extra="ignore"`` the rest, so LINE adding new event kinds never 500s the
webhook. See https://developers.line.biz/en/reference/messaging-api/#webhooks
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_float(v: Any) -> Any:
    """Reject float inputs so an epoch-ms timestamp can't be silently coerced.

    Mirrors the project-wide "never trust a float at the boundary" rule
    (see CLAUDE.md). ``bool`` is an ``int`` subclass we don't expect here,
    but LINE only ever sends an integer, so a plain float guard is enough.
    """
    if isinstance(v, float):
        raise ValueError("timestamp must be an integer, not a float")
    return v


class LineSource(BaseModel):
    """Who/where the event came from. ``user_id`` is the push target."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    type: str | None = None  # "user" | "group" | "room"
    user_id: str | None = Field(default=None, alias="userId")
    group_id: str | None = Field(default=None, alias="groupId")
    room_id: str | None = Field(default=None, alias="roomId")


class LineEventMessage(BaseModel):
    """The ``message`` object on a message-type event."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    type: str  # "text" | "image" | "sticker" | ...
    id: str | None = None
    text: str | None = None  # populated when type == "text"


class LinePostback(BaseModel):
    """The ``postback`` object — data set on a button/quick-reply action."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    data: str | None = None


class LineWebhookEvent(BaseModel):
    """A single webhook event. ``reply_token`` is valid for ~60 seconds."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    type: str  # "message" | "follow" | "unfollow" | "postback" | ...
    reply_token: str | None = Field(default=None, alias="replyToken")
    timestamp: Annotated[int | None, BeforeValidator(_reject_float)] = None
    source: LineSource | None = None
    message: LineEventMessage | None = None
    postback: LinePostback | None = None


class LineWebhookBody(BaseModel):
    """Top-level webhook payload."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    destination: str | None = None
    events: list[LineWebhookEvent] = Field(default_factory=list)


__all__ = [
    "LineEventMessage",
    "LinePostback",
    "LineSource",
    "LineWebhookBody",
    "LineWebhookEvent",
]
