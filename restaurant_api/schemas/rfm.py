"""Pydantic v2 schemas + segment enum for RFM 分眾 endpoints.

``RfmSegment`` lives here (not in the service) so both the service and the
schemas can share it without an import cycle.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field


class RfmSegment(enum.StrEnum):
    """Mutually-exclusive RFM cohort labels."""

    CHAMPION = "champion"
    LOYAL = "loyal"
    POTENTIAL = "potential"
    NEW = "new"
    AT_RISK = "at_risk"
    DORMANT = "dormant"


class SegmentBucket(BaseModel):
    """Member count for one RFM segment."""

    model_config = ConfigDict(frozen=True)

    segment: RfmSegment
    count: int


class SegmentsResponse(BaseModel):
    """RFM segment distribution across all live members."""

    model_config = ConfigDict(frozen=True)

    total_scored: int
    segments: list[SegmentBucket]


class SegmentBroadcastRequest(BaseModel):
    """Body for a segment LINE broadcast."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1, max_length=500)


class BroadcastResult(BaseModel):
    """Outcome of a segment broadcast."""

    model_config = ConfigDict(frozen=True)

    segment: RfmSegment
    audience_size: int  # segment members with a LINE id
    delivered: int  # count the messenger reports delivered


__all__ = [
    "BroadcastResult",
    "RfmSegment",
    "SegmentBroadcastRequest",
    "SegmentBucket",
    "SegmentsResponse",
]
