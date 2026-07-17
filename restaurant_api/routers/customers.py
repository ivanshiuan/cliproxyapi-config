"""FastAPI router — ``/customers`` (member master + points history).

Endpoints
---------
- POST   /customers                          create a new member
- GET    /customers/{id}                     fetch one
- GET    /customers                          list / search (counter lookup)
- PATCH  /customers/{id}                     update display_name / tier / handles
- DELETE /customers/{id}                     soft-delete (sets deleted_at)
- GET    /customers/{id}/points-ledger       read-only points history

Append-only contract: the points ledger has no POST / PATCH / DELETE
here — earn rows are written by the orders close path, redeem rows
will land on a future ``POST /customers/{id}/redeem`` endpoint, and
expiry sweeps come from the nightly job. The OpenAPI immutability
test asserts the absence of write endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, Messenger, TenantId
from ..models import CustomerTier
from ..schemas.customers import (
    CustomerCreate,
    CustomerPointsLedgerResponse,
    CustomerResponse,
    CustomerUpdate,
    PointsRedemptionRequest,
)
from ..schemas.referral import ReferralCodeResponse, ReferralResponse
from ..schemas.stored_value import (
    StoredValueBalanceResponse,
    StoredValueLedgerResponse,
    StoredValueSpendRequest,
    StoredValueTopUpRequest,
)
from ..services import customers_service, referral_service, stored_value_service

# Module-level Query() singletons (B008 / FastAPI metadata).
_Q_SEARCH = Query(default=None, max_length=80)
_Q_TIER = Query(default=None)
_Q_INCLUDE_DELETED = Query(default=False)
_Q_LIMIT = Query(default=200, ge=1, le=500)

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增會員 (顧客自願填或櫃台代填)",
)
async def create_customer(
    payload: CustomerCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> CustomerResponse:
    return await customers_service.create_customer(
        session, payload, tenant_id=tenant_id
    )


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="查單筆會員",
)
async def get_customer(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> CustomerResponse:
    return await customers_service.get_customer(
        session, customer_id, tenant_id=tenant_id
    )


@router.get(
    "",
    response_model=list[CustomerResponse],
    summary="搜尋會員 (display_name / phone / line_user_id 前綴比對)",
)
async def list_customers(
    session: DbSession,
    tenant_id: TenantId,
    search: str | None = _Q_SEARCH,
    tier: CustomerTier | None = _Q_TIER,
    include_deleted: bool = _Q_INCLUDE_DELETED,
    limit: int = _Q_LIMIT,
) -> list[CustomerResponse]:
    return await customers_service.list_customers(
        session,
        tenant_id=tenant_id,
        search=search,
        tier=tier,
        include_deleted=include_deleted,
        limit=limit,
    )


@router.patch(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="部分更新會員資料",
)
async def patch_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> CustomerResponse:
    return await customers_service.patch_customer(
        session, customer_id, payload, tenant_id=tenant_id
    )


@router.delete(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="軟刪除 (sets deleted_at, 保留歷史紀錄)",
)
async def soft_delete_customer(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> CustomerResponse:
    return await customers_service.soft_delete_customer(
        session, customer_id, tenant_id=tenant_id
    )


@router.get(
    "/{customer_id}/points-ledger",
    response_model=list[CustomerPointsLedgerResponse],
    summary="會員點數歷史 (newest first; append-only)",
)
async def list_points_ledger(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    limit: int = _Q_LIMIT,
) -> list[CustomerPointsLedgerResponse]:
    return await customers_service.list_points_ledger(
        session, customer_id, tenant_id=tenant_id, limit=limit
    )


@router.post(
    "/{customer_id}/redeem",
    response_model=CustomerPointsLedgerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="點數兌換 (寫入負值 ledger, 扣除餘額)",
)
async def redeem_points(
    customer_id: uuid.UUID,
    payload: PointsRedemptionRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CustomerPointsLedgerResponse:
    return await customers_service.redeem_points(
        session, customer_id, payload, tenant_id=tenant_id
    )


# ──────────────────────────────────────────────────────────────────────────
# 儲值 (stored-value / prepaid wallet)
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/{customer_id}/stored-value/top-up",
    response_model=StoredValueBalanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="儲值 (寫入 topup + 加贈 ledger, 回傳新餘額)",
)
async def stored_value_top_up(
    customer_id: uuid.UUID,
    payload: StoredValueTopUpRequest,
    session: DbSession,
    tenant_id: TenantId,
    messenger: Messenger,
) -> StoredValueBalanceResponse:
    return await stored_value_service.top_up(
        session, customer_id, payload, tenant_id=tenant_id, messenger=messenger
    )


@router.post(
    "/{customer_id}/stored-value/spend",
    response_model=StoredValueBalanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="儲值支付 (寫入負值 ledger, 扣除餘額; 餘額不足回 409)",
)
async def stored_value_spend(
    customer_id: uuid.UUID,
    payload: StoredValueSpendRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> StoredValueBalanceResponse:
    return await stored_value_service.spend(
        session, customer_id, payload, tenant_id=tenant_id
    )


@router.get(
    "/{customer_id}/stored-value",
    response_model=StoredValueBalanceResponse,
    summary="會員儲值餘額",
)
async def stored_value_balance(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> StoredValueBalanceResponse:
    return await stored_value_service.get_balance(
        session, customer_id, tenant_id=tenant_id
    )


@router.get(
    "/{customer_id}/stored-value/ledger",
    response_model=list[StoredValueLedgerResponse],
    summary="會員儲值歷史 (newest first; append-only)",
)
async def stored_value_ledger(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    limit: int = _Q_LIMIT,
) -> list[StoredValueLedgerResponse]:
    return await stored_value_service.list_ledger(
        session, customer_id, tenant_id=tenant_id, limit=limit
    )


# ──────────────────────────────────────────────────────────────────────────
# 裂變 (referral)
# ──────────────────────────────────────────────────────────────────────────


@router.get(
    "/{customer_id}/referral-code",
    response_model=ReferralCodeResponse,
    summary="會員專屬邀請碼 (首次取用時自動產生)",
)
async def get_referral_code(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> ReferralCodeResponse:
    cid, code = await referral_service.get_or_create_code(
        session, customer_id, tenant_id=tenant_id
    )
    return ReferralCodeResponse(customer_id=cid, referral_code=code)


@router.get(
    "/{customer_id}/referrals",
    response_model=list[ReferralResponse],
    summary="此會員推薦的名單 (newest first)",
)
async def list_referrals(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    limit: int = _Q_LIMIT,
) -> list[ReferralResponse]:
    rows = await referral_service.list_referrals_for(
        session, customer_id, tenant_id=tenant_id, limit=limit
    )
    return [ReferralResponse.model_validate(r) for r in rows]


__all__ = ["router"]
