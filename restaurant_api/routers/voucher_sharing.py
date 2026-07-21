"""``/vouchers`` router — 送券裂變 (模式 A) + 貴人配額券 (模式 B).

模式 A (member-facing + counter):
- ``POST /vouchers/gifts``                     member mints a share link
- ``POST /vouchers/gifts/{share_token}/claim`` friend claims it (加好友 後)
- ``POST /vouchers/gifts/{gift_id}/redeem``    counter redeems → sender rewarded (admin)

模式 B (operator/counter, admin-gated):
- ``POST /vouchers/grants``                    create a capped allotment
- ``POST /vouchers/grants/{grant_id}/distribute`` draw one from the allotment
- ``GET  /vouchers/grants``                    allotment dashboard

Redemption and grant management are counter/operator actions → admin-gated
(``require_admin``), consistent with the campaigns counter endpoints. Minting and
claiming are member-facing and stay public.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from ..api.auth import require_admin
from ..api.deps import DbSession, Messenger, TenantId
from ..schemas.voucher_sharing import (
    GiftClaimRequest,
    GiftCreateRequest,
    GrantCreateRequest,
    VoucherGiftResponse,
    VoucherGrantResponse,
)
from ..services import voucher_gift_service, voucher_grant_service

_ADMIN = [Depends(require_admin)]

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


# ── 模式 A: gifts ─────────────────────────────────────────────────────────────


@router.post(
    "/gifts",
    response_model=VoucherGiftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="會員產生送券連結 (pending)",
)
async def create_gift(
    payload: GiftCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> VoucherGiftResponse:
    gift = await voucher_gift_service.create_gift(
        session,
        sender_id=payload.sender_id,
        tenant_id=tenant_id,
        reward_kind=payload.reward_kind,
        reward_amount=payload.reward_amount,
    )
    return VoucherGiftResponse.model_validate(gift)


@router.post(
    "/gifts/{share_token}/claim",
    response_model=VoucherGiftResponse,
    summary="朋友領券 (加好友後綁定; 不能領自己送的; 已領回 409)",
)
async def claim_gift(
    share_token: str,
    payload: GiftClaimRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> VoucherGiftResponse:
    gift = await voucher_gift_service.claim_gift(
        session,
        share_token=share_token,
        recipient_id=payload.recipient_id,
        tenant_id=tenant_id,
    )
    return VoucherGiftResponse.model_validate(gift)


@router.post(
    "/gifts/{gift_id}/redeem",
    response_model=VoucherGiftResponse,
    summary="櫃台核銷送券 → 送券人回饋入帳 (一次性; 已核回 409)",
    dependencies=_ADMIN,
)
async def redeem_gift(
    gift_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    messenger: Messenger,
) -> VoucherGiftResponse:
    gift = await voucher_gift_service.redeem_gift(
        session, gift_id=gift_id, tenant_id=tenant_id, messenger=messenger
    )
    return VoucherGiftResponse.model_validate(gift)


# ── 模式 B: grants ────────────────────────────────────────────────────────────


@router.post(
    "/grants",
    response_model=VoucherGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立貴人配額券 (有 total_quota 硬上限)",
    dependencies=_ADMIN,
)
async def create_grant(
    payload: GrantCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> VoucherGrantResponse:
    grant = await voucher_grant_service.create_grant(
        session,
        tenant_id=tenant_id,
        grantee_name=payload.grantee_name,
        voucher_kind=payload.voucher_kind,
        total_quota=payload.total_quota,
        valid_until=payload.valid_until,
        face_value=payload.face_value,
        menu_item_id=payload.menu_item_id,
        grantee_id=payload.grantee_id,
    )
    return VoucherGrantResponse.model_validate(grant)


@router.post(
    "/grants/{grant_id}/distribute",
    response_model=VoucherGrantResponse,
    summary="從配額包發出一張 (額度用盡回 409; 過期回 409)",
    dependencies=_ADMIN,
)
async def distribute_grant(
    grant_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> VoucherGrantResponse:
    grant = await voucher_grant_service.distribute(
        session, grant_id=grant_id, tenant_id=tenant_id
    )
    return VoucherGrantResponse.model_validate(grant)


@router.get(
    "/grants",
    response_model=list[VoucherGrantResponse],
    summary="貴人配額券儀表 (每人發了幾張/剩多少; newest first)",
    dependencies=_ADMIN,
)
async def list_grants(
    session: DbSession,
    tenant_id: TenantId,
) -> list[VoucherGrantResponse]:
    grants = await voucher_grant_service.list_grants(session, tenant_id=tenant_id)
    return [VoucherGrantResponse.model_validate(g) for g in grants]


__all__ = ["router"]
