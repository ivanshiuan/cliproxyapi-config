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

import html
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import HTMLResponse

from ..api.deps import DbSession, Messenger, TenantId
from ..models import CampaignStatus, VoucherStatus
from ..qr import make_qr_svg
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


# ── QR code / printable poster (門口擺放) ───────────────────────────────────

# Brand passthrough params baked into the demo URL (and the poster), so one
# QR carries the store's theme. All optional; the page has tasteful defaults.
_BRAND_PARAMS = ("brand", "tagline", "logo", "primary", "accent", "bg")


def _demo_url(request: Request, campaign_id: uuid.UUID, base_url: str | None, brand: dict[str, str]) -> str:
    """Build the player-facing demo URL the QR points at."""
    root = (base_url or str(request.base_url)).rstrip("/")
    params = {"campaign": str(campaign_id), **brand}
    return f"{root}/demo/?{urlencode(params)}"


@router.get(
    "/{campaign_id}/qr.svg",
    response_class=Response,
    summary="輪盤抽獎 QR Code (SVG, 可印海報)",
)
async def campaign_qr(
    campaign_id: uuid.UUID,
    request: Request,
    session: DbSession,
    tenant_id: TenantId,
    base_url: str | None = None,
    brand: str | None = None,
    tagline: str | None = None,
    logo: str | None = None,
    primary: str | None = None,
    accent: str | None = None,
    bg: str | None = None,
) -> Response:
    # 404 + tenant scope via the normal loader.
    await campaigns_service.get_campaign(session, campaign_id, tenant_id=tenant_id)
    brand_params = {k: v for k, v in locals().items() if k in _BRAND_PARAMS and v is not None}
    url = _demo_url(request, campaign_id, base_url, brand_params)
    svg = make_qr_svg(url)
    return Response(content=svg, media_type="image/svg+xml")


@router.get(
    "/{campaign_id}/poster",
    response_class=HTMLResponse,
    summary="門口海報 (campaign 名稱 + 標語 + 內嵌 QR, 可列印)",
)
async def campaign_poster(
    campaign_id: uuid.UUID,
    request: Request,
    session: DbSession,
    tenant_id: TenantId,
    base_url: str | None = None,
    brand: str | None = None,
    tagline: str | None = None,
    logo: str | None = None,
    primary: str | None = None,
    accent: str | None = None,
    bg: str | None = None,
) -> HTMLResponse:
    campaign = await campaigns_service.get_campaign(session, campaign_id, tenant_id=tenant_id)
    brand_params = {k: v for k, v in locals().items() if k in _BRAND_PARAMS and v is not None}
    url = _demo_url(request, campaign_id, base_url, brand_params)
    svg = make_qr_svg(url, box_size=8).decode("utf-8")

    title = html.escape(brand or campaign.name)
    sub = html.escape(tagline or "掃碼抽獎 · 加入會員 · 來店兌換")
    primary_c = primary or "#ff5d8f"
    accent_c = accent or "#ffd34e"
    logo_html = (
        f'<img src="{html.escape(logo)}" alt="logo" style="max-height:90px;margin-bottom:18px" />'
        if logo
        else ""
    )
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} · 開幕輪盤海報</title>
<style>
  @page {{ size: A4; margin: 0; }}
  body {{ margin:0; font-family:"PingFang TC","Noto Sans TC",system-ui,sans-serif;
    min-height:100vh; display:grid; place-items:center;
    background:linear-gradient(160deg,{accent_c}22,{primary_c}22); color:#1b1033; }}
  .poster {{ width:min(92vw,640px); aspect-ratio:1/1.414; background:#fff; border-radius:24px;
    box-shadow:0 20px 60px rgba(0,0,0,.18); padding:48px 40px; text-align:center;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; }}
  .kicker {{ letter-spacing:6px; color:{primary_c}; font-weight:800; font-size:16px; }}
  h1 {{ font-size:40px; margin:6px 0; line-height:1.15; }}
  .sub {{ color:#555; font-size:18px; }}
  .qr {{ width:320px; height:320px; margin:14px auto; padding:16px; border:6px solid {accent_c};
    border-radius:18px; }}
  .qr svg {{ width:100%; height:100%; }}
  .cta {{ font-size:22px; font-weight:900; color:{primary_c}; }}
  .foot {{ color:#888; font-size:13px; }}
  @media print {{ body {{ background:#fff; }} .poster {{ box-shadow:none; }} }}
</style></head>
<body><div class="poster">
  {logo_html}
  <div class="kicker">GRAND OPENING</div>
  <h1>{title}</h1>
  <div class="sub">{sub}</div>
  <div class="qr">{svg}</div>
  <div class="cta">📱 掃我抽大獎</div>
  <div class="foot">最大獎 免單四人套餐 · 每日一抽 · 抽中即加入會員</div>
</div></body></html>"""
    return HTMLResponse(content=page)


__all__ = ["router"]
