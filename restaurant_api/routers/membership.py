"""``/membership`` router — 成效報表 dashboard (read-only analytics)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from ..api.deps import DbSession, Messenger, TenantId
from ..schemas.membership_stats import MembershipStatsResponse
from ..schemas.rfm import (
    BroadcastResult,
    RfmSegment,
    SegmentBroadcastRequest,
    SegmentsResponse,
)
from ..services import membership_stats_service, rfm_service

router = APIRouter(prefix="/membership", tags=["membership"])

# Optional rolling window for the flow metrics (1..365 days). Omit = lifetime.
_Q_DAYS = Query(default=None, ge=1, le=365)


@router.get(
    "/stats",
    response_model=MembershipStatsResponse,
    summary="會員成長成效報表 (tier / 點數 / 儲值 / 裂變 / UGC 聚合快照; ?days=N 限定流量區間)",
)
async def membership_stats(
    session: DbSession,
    tenant_id: TenantId,
    days: int | None = _Q_DAYS,
) -> MembershipStatsResponse:
    since = datetime.now(UTC) - timedelta(days=days) if days else None
    return await membership_stats_service.compute_stats(
        session, tenant_id=tenant_id, since=since, window_days=days
    )


@router.get(
    "/segments",
    response_model=SegmentsResponse,
    summary="RFM 分眾分布 (champion / loyal / potential / new / at_risk / dormant)",
)
async def membership_segments(
    session: DbSession,
    tenant_id: TenantId,
) -> SegmentsResponse:
    return await rfm_service.segment_counts(session, tenant_id=tenant_id)


@router.post(
    "/segments/{segment}/broadcast",
    response_model=BroadcastResult,
    summary="對指定 RFM 分眾發 LINE 分眾推播 (回傳投遞數)",
)
async def broadcast_segment(
    segment: RfmSegment,
    payload: SegmentBroadcastRequest,
    session: DbSession,
    tenant_id: TenantId,
    messenger: Messenger,
) -> BroadcastResult:
    return await rfm_service.broadcast_to_segment(
        session, tenant_id=tenant_id, segment=segment, message=payload.message, messenger=messenger
    )


__all__ = ["router"]
