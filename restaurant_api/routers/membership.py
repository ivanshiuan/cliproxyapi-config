"""``/membership`` router — 成效報表 dashboard (read-only analytics)."""

from __future__ import annotations

from fastapi import APIRouter

from ..api.deps import DbSession, TenantId
from ..schemas.membership_stats import MembershipStatsResponse
from ..services import membership_stats_service

router = APIRouter(prefix="/membership", tags=["membership"])


@router.get(
    "/stats",
    response_model=MembershipStatsResponse,
    summary="會員成長成效報表 (tier / 點數 / 儲值 / 裂變 / UGC 聚合快照)",
)
async def membership_stats(
    session: DbSession,
    tenant_id: TenantId,
) -> MembershipStatsResponse:
    return await membership_stats_service.compute_stats(session, tenant_id=tenant_id)


__all__ = ["router"]
