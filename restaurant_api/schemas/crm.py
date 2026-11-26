"""Pydantic v2 schemas for the ``/crm`` router — CRM 關係經營層 (對標 Twenty CRM).

Four writeable resources (notes / tasks / tags / interactions) plus the
read-only **unified customer timeline** that merges orders, points,
stored value, referrals and the CRM rows into one feed.

Conventions (same as every schema module in this repo):
- Inputs are ``frozen=True``; money stays ``Decimal`` (floats rejected via
  ``StrictDecimal`` — only used on the read side here, but kept for parity).
- Caller-supplied datetimes must be tz-aware (``AwareDatetime``).
- Every datetime in a response is rendered in ``Asia/Taipei`` by the
  ``TaipeiDatetime`` after-validator, so services can hand ORM rows (UTC)
  straight to ``model_validate``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from ..models import CustomerTaskStatus, InteractionChannel, InteractionDirection

TPE = ZoneInfo("Asia/Taipei")


def _reject_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("float not allowed; use Decimal or string")
    return v


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


def _to_tpe(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TPE)


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]
TaipeiDatetime = Annotated[datetime, AfterValidator(_to_tpe)]

NoteBody = Annotated[str, BeforeValidator(_strip), Field(min_length=1, max_length=2000)]
TaskTitle = Annotated[str, BeforeValidator(_strip), Field(min_length=1, max_length=200)]
TaskBody = Annotated[str, Field(max_length=4000)]
TagName = Annotated[str, BeforeValidator(_strip), Field(min_length=1, max_length=40)]
TagColor = Annotated[str, BeforeValidator(_strip), Field(min_length=1, max_length=16)]
Summary = Annotated[str, BeforeValidator(_strip), Field(min_length=1, max_length=2000)]


def _reject_explicit_null(model: BaseModel, fields: tuple[str, ...]) -> None:
    """PATCH helper: a field may be omitted, but not sent as ``null`` when
    the column is NOT NULL."""
    for f in fields:
        if f in model.model_fields_set and getattr(model, f) is None:
            raise ValueError(f"{f} cannot be null")


# ──────────────────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────────────────


class NoteCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    body: NoteBody
    author_employee_id: UUID | None = None


class NotePatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    body: NoteBody


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID
    author_employee_id: UUID | None
    body: str
    created_at: TaipeiDatetime
    updated_at: TaipeiDatetime


# ──────────────────────────────────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────────────────────────────────


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: TaskTitle
    body: TaskBody | None = None
    assignee_employee_id: UUID | None = None
    due_at: AwareDatetime | None = None
    created_by_employee_id: UUID | None = None


class TaskPatchRequest(BaseModel):
    """Partial update. ``body`` / ``assignee_employee_id`` / ``due_at`` may be
    explicitly nulled; ``title`` / ``status`` may only be omitted."""

    model_config = ConfigDict(frozen=True)

    title: TaskTitle | None = None
    body: TaskBody | None = None
    assignee_employee_id: UUID | None = None
    due_at: AwareDatetime | None = None
    status: CustomerTaskStatus | None = None

    @model_validator(mode="after")
    def _no_null_required(self) -> TaskPatchRequest:
        _reject_explicit_null(self, ("title", "status"))
        return self


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID
    title: str
    body: str | None
    assignee_employee_id: UUID | None
    created_by_employee_id: UUID | None
    due_at: TaipeiDatetime | None
    status: CustomerTaskStatus
    completed_at: TaipeiDatetime | None
    created_at: TaipeiDatetime
    updated_at: TaipeiDatetime


# ──────────────────────────────────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────────────────────────────────


class TagCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: TagName
    color: TagColor | None = None


class TagPatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: TagName | None = None
    color: TagColor | None = None

    @model_validator(mode="after")
    def _no_null_name(self) -> TagPatchRequest:
        _reject_explicit_null(self, ("name",))
        return self


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    color: str | None
    created_at: TaipeiDatetime
    updated_at: TaipeiDatetime


class TagAttachRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tag_id: UUID


class CustomerTagResponse(BaseModel):
    """A tag as attached to one customer."""

    model_config = ConfigDict(frozen=True)

    link_id: UUID
    customer_id: UUID
    tag: TagResponse
    attached_at: TaipeiDatetime


# ──────────────────────────────────────────────────────────────────────────
# Interactions
# ──────────────────────────────────────────────────────────────────────────


class InteractionCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: InteractionChannel
    direction: InteractionDirection
    summary: Summary
    employee_id: UUID | None = None
    occurred_at: AwareDatetime | None = None


class InteractionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID
    channel: InteractionChannel
    direction: InteractionDirection
    summary: str
    employee_id: UUID | None
    occurred_at: TaipeiDatetime
    created_at: TaipeiDatetime


# ──────────────────────────────────────────────────────────────────────────
# Timeline
# ──────────────────────────────────────────────────────────────────────────

TimelineEventType = Literal[
    "order", "points", "stored_value", "referral", "note", "task", "interaction"
]


class TimelineEvent(BaseModel):
    """One entry in the unified customer timeline."""

    model_config = ConfigDict(frozen=True)

    type: TimelineEventType
    occurred_at: TaipeiDatetime
    title: str
    detail: str | None = None
    ref_id: UUID
    amount: StrictDecimal | None = None


class TimelineResponse(BaseModel):
    """``events`` newest-first; ``next_before`` is the cursor for the next
    page (the last event's ``occurred_at``) or ``null`` when the page came
    back short (no more history)."""

    model_config = ConfigDict(frozen=True)

    events: list[TimelineEvent]
    next_before: TaipeiDatetime | None


__all__ = [
    "CustomerTagResponse",
    "InteractionCreateRequest",
    "InteractionResponse",
    "NoteCreateRequest",
    "NotePatchRequest",
    "NoteResponse",
    "StrictDecimal",
    "TagAttachRequest",
    "TagCreateRequest",
    "TagPatchRequest",
    "TagResponse",
    "TaskCreateRequest",
    "TaskPatchRequest",
    "TaskResponse",
    "TimelineEvent",
    "TimelineEventType",
    "TimelineResponse",
]
