"""FastAPI router — ``/campaigns`` (開幕引流輪盤抽獎 wheel-spin lottery).

Endpoint map
------------
Config (operator):
- POST   /campaigns                                  create a campaign
- GET    /campaigns                                  list campaigns
- GET    /campaigns/{id}                             fetch one
- PATCH  /campaigns/{id}                             update config / status
- POST   /campaigns/{id}/prizes                      add a wheel segment
- GET    /campaigns/{id}/prizes                      list segments
- PATCH  /campaigns/{id}/prizes/{prize_id}           update a segment

Player:
- GET    /campaigns/{id}/wheel                       public wheel layout
- POST   /campaigns/{id}/spin                        spin once (auto-joins member)

Wallet / counter:
- GET    /campaigns/{id}/vouchers                    a member's wallet
- GET    /campaigns/{id}/vouchers/by-code/{code}     look up by redemption code
- POST   /campaigns/{id}/vouchers/{voucher_id}/redeem  redeem one (1 per visit/day)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, Messenger, TenantId
from ..models import CampaignStatus, VoucherStatus
from ..schemas.campaigns import (
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    PrizeCreate,
    PrizeResponse,
    PrizeUpdate,
    SpinRequest,
    SpinResponse,
    VoucherRedeemRequest,
    VoucherResponse,
    WheelResponse,
)
from ..services import campaigns_service

# Module-level Query() singletons (B008 / FastAPI metadata).
_Q_STATUS = Query(default=None)
_Q_VOUCHER_STATUS = Query(default=None)
_Q_INCLUDE_DELETED = Query(default=False)
_Q_INCLUDE_INACTIVE = Query(default=True)
_Q_LIMIT = Query(default=200, ge=1, le=500)
_Q_CUSTOMER_ID = Query(...)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ── Campaign config ────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立輪盤抽獎活動",
)
async def create_campaign(
    payload: CampaignCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> CampaignResponse:
    return await campaigns_service.create_campaign(session, payload, tenant_id=tenant_id)


@router.get("", response_model=list[CampaignResponse], summary="列出活動")
async def list_campaigns(
    session: DbSession,
    tenant_id: TenantId,
    campaign_status: CampaignStatus | None = _Q_STATUS,
    include_deleted: bool = _Q_INCLUDE_DELETED,
    limit: int = _Q_LIMIT,
) -> list[CampaignResponse]:
    return await campaigns_service.list_campaigns(
        session,
        tenant_id=tenant_id,
        status=campaign_status,
        include_deleted=include_deleted,
        limit=limit,
    )


@router.get("/{campaign_id}", response_model=CampaignResponse, summary="查單一活動")
async def get_campaign(
    campaign_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> CampaignResponse:
    return await campaigns_service.get_campaign(session, campaign_id, tenant_id=tenant_id)


@router.patch(
    "/{campaign_id}",
    response_model=CampaignResponse,
    summary="更新活動設定 / 狀態 (draft→active→ended)",
)
async def patch_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> CampaignResponse:
    return await campaigns_service.patch_campaign(
        session, campaign_id, payload, tenant_id=tenant_id
    )


# ── Prizes ─────────────────────────────────────────────────────────────────


@router.post(
    "/{campaign_id}/prizes",
    response_model=PrizeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增輪盤獎項 (大獎用低 weight + 低 total_quota 控管稀有度)",
)
async def add_prize(
    campaign_id: uuid.UUID,
    payload: PrizeCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> PrizeResponse:
    return await campaigns_service.add_prize(
        session, campaign_id, payload, tenant_id=tenant_id
    )


@router.get(
    "/{campaign_id}/prizes",
    response_model=list[PrizeResponse],
    summary="列出獎項",
)
async def list_prizes(
    campaign_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    include_inactive: bool = _Q_INCLUDE_INACTIVE,
) -> list[PrizeResponse]:
    return await campaigns_service.list_prizes(
        session, campaign_id, tenant_id=tenant_id, include_inactive=include_inactive
    )


@router.patch(
    "/{campaign_id}/prizes/{prize_id}",
    response_model=PrizeResponse,
    summary="更新獎項 (weight / quota / value)",
)
async def patch_prize(
    campaign_id: uuid.UUID,
    prize_id: uuid.UUID,
    payload: PrizeUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> PrizeResponse:
    return await campaigns_service.patch_prize(
        session, campaign_id, prize_id, payload, tenant_id=tenant_id
    )


# ── Player ─────────────────────────────────────────────────────────────────


@router.get(
    "/{campaign_id}/wheel",
    response_model=WheelResponse,
    summary="輪盤公開版面 (前端渲染用; 不含機率)",
)
async def get_wheel(
    campaign_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> WheelResponse:
    return await campaigns_service.get_wheel(session, campaign_id, tenant_id=tenant_id)


@router.post(
    "/{campaign_id}/spin",
    response_model=SpinResponse,
    status_code=status.HTTP_201_CREATED,
    summary="抽獎一次 (每日限抽; 首抽自動加入會員; 推播每日訊息)",
)
async def spin(
    campaign_id: uuid.UUID,
    payload: SpinRequest,
    session: DbSession,
    tenant_id: TenantId,
    messenger: Messenger,
) -> SpinResponse:
    return await campaigns_service.spin(
        session, campaign_id, payload, tenant_id=tenant_id, messenger=messenger
    )


# ── Wallet / redemption ────────────────────────────────────────────────────


@router.get(
    "/{campaign_id}/vouchers",
    response_model=list[VoucherResponse],
    summary="會員獎品錢包 (價值高→低; 來店時選最大獎核銷)",
)
async def list_vouchers(
    campaign_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    customer_id: uuid.UUID = _Q_CUSTOMER_ID,
    voucher_status: VoucherStatus | None = _Q_VOUCHER_STATUS,
    limit: int = _Q_LIMIT,
) -> list[VoucherResponse]:
    return await campaigns_service.list_vouchers(
        session,
        campaign_id,
        tenant_id=tenant_id,
        customer_id=customer_id,
        status=voucher_status,
        limit=limit,
    )


@router.get(
    "/{campaign_id}/vouchers/by-code/{code}",
    response_model=VoucherResponse,
    summary="以兌換碼查券 (櫃台掃碼)",
)
async def get_voucher_by_code(
    campaign_id: uuid.UUID,
    code: str,
    session: DbSession,
    tenant_id: TenantId,
) -> VoucherResponse:
    return await campaigns_service.get_voucher_by_code(
        session, campaign_id, code, tenant_id=tenant_id
    )


@router.post(
    "/{campaign_id}/vouchers/{voucher_id}/redeem",
    response_model=VoucherResponse,
    summary="核銷一張券 (每位會員每日/每次來店限核銷一張)",
)
async def redeem_voucher(
    campaign_id: uuid.UUID,
    voucher_id: uuid.UUID,
    payload: VoucherRedeemRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> VoucherResponse:
    return await campaigns_service.redeem_voucher(
        session, campaign_id, voucher_id, payload, tenant_id=tenant_id
    )


__all__ = ["router"]
