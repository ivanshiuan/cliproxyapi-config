"""``/membership`` router — 成效報表 dashboard (read-only analytics)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from ..api.deps import DbSession, TenantId
from ..schemas.membership_stats import MembershipStatsResponse
from ..services import membership_stats_service

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


__all__ = ["router"]
