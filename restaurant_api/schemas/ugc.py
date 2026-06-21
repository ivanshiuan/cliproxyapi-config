"""Pydantic v2 schemas for the UGC (打卡 / 評論換獎) endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import UgcKind, UgcStatus


class UgcSubmitRequest(BaseModel):
    """A member claims they posted a review / check-in (goes into the queue)."""

    model_config = ConfigDict(frozen=True)

    customer_id: UUID
    kind: UgcKind
    proof_url: str | None = Field(default=None, max_length=2000)


class UgcReviewRequest(BaseModel):
    """Staff decision on a pending submission (approve / reject)."""

    model_config = ConfigDict(frozen=True)

    reviewed_by: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class UgcSubmissionResponse(BaseModel):
    """A submission row projected back to HTTP."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    kind: UgcKind
    proof_url: str | None
    status: UgcStatus
    reward_points: Decimal
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    note: str | None
    created_at: datetime


__all__ = [
    "UgcReviewRequest",
    "UgcSubmissionResponse",
    "UgcSubmitRequest",
]
