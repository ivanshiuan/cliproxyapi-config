"""FastAPI router — ``/discount-rules`` (自動折扣規則, docs/22 #10).

Thin parse-validate-respond layer over ``services/discount_rule_service``.
Application happens automatically inside the POS money paths; these
endpoints only manage the rule catalog.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, TenantId
from ..schemas.discount_rules import (
    DiscountRuleCreate,
    DiscountRuleResponse,
    DiscountRuleUpdate,
)
from ..services import discount_rule_service

_Q_STORE_ID = Query(default=None)
_Q_INCLUDE_INACTIVE = Query(default=False)

router = APIRouter(prefix="/discount-rules", tags=["discount-rules"])


@router.post(
    "",
    response_model=DiscountRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立自動折扣規則 (滿額/時段)",
)
async def create_rule(
    payload: DiscountRuleCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> DiscountRuleResponse:
    return await discount_rule_service.create_rule(session, payload, tenant_id=tenant_id)


@router.get(
    "",
    response_model=list[DiscountRuleResponse],
    summary="列出自動折扣規則",
)
async def list_rules(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = _Q_STORE_ID,
    include_inactive: bool = _Q_INCLUDE_INACTIVE,
) -> list[DiscountRuleResponse]:
    return await discount_rule_service.list_rules(
        session, tenant_id=tenant_id, store_id=store_id, include_inactive=include_inactive
    )


@router.patch(
    "/{rule_id}",
    response_model=DiscountRuleResponse,
    summary="啟用/停用自動折扣規則",
)
async def update_rule(
    rule_id: uuid.UUID,
    payload: DiscountRuleUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> DiscountRuleResponse:
    return await discount_rule_service.update_rule(session, rule_id, payload, tenant_id=tenant_id)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除自動折扣規則 (軟刪)",
)
async def delete_rule(
    rule_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await discount_rule_service.delete_rule(session, rule_id, tenant_id=tenant_id)


__all__ = ["router"]
