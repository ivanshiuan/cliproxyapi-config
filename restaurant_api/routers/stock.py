"""``/stock`` router — stock intake, adjustments, and ledger reads.

Thin HTTP surface — every behaviour lives in :mod:`restaurant_api.services.stock_service`.
The router's job here is solely:

  * parse Pydantic v2 inputs (which already reject ``float`` qty/unit_cost),
  * inject the tenant + DB session,
  * map the service's 200/201 distinction (idempotent replay vs. new insert)
    onto HTTP status,
  * propagate ``DomainError`` subclasses up to the global error handler.

We do **not** open transactions here — the request-scoped session does that.
The router never calls ``UPDATE``/``DELETE`` on ``stock_movements`` (the ledger
is append-only — see :mod:`stock_service`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from ..api.deps import DbSession, TenantId
from ..models import MovementType
from ..schemas.stock import (
    AdjustmentCreateRequest,
    AdjustmentResponse,
    PurchaseCreateRequest,
    PurchaseResponse,
    StockMovementResponse,
)
from ..services import stock_service

router = APIRouter(prefix="/stock", tags=["stock"])


# Annotated query-param aliases — keeps signatures short and dodges Ruff B008
# (function calls in default args). FastAPI requires the default to live on
# the parameter itself (``= None``), not inside ``Query(...)``, so these
# carry only validation metadata.
StoreIdQ = Annotated[str | None, Query(description="UUID filter")]
IngredientIdQ = Annotated[str | None, Query(description="UUID filter")]
FromDateQ = Annotated[date | None, Query()]
ToDateQ = Annotated[date | None, Query()]
SupplierNameQ = Annotated[str | None, Query(max_length=100)]
MovementTypeQ = Annotated[MovementType | None, Query()]
FromTsQ = Annotated[datetime | None, Query()]
ToTsQ = Annotated[datetime | None, Query()]
LimitQ = Annotated[int, Query(ge=1, le=200)]
OffsetQ = Annotated[int, Query(ge=0)]


# ──────────────────────────────────────────────────────────────────────────
# Purchases
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/purchases",
    response_model=PurchaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a supplier purchase invoice (N stock_movements of type purchase).",
)
async def create_purchase(
    payload: PurchaseCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
    response: Response,
) -> PurchaseResponse:
    purchase, created = await stock_service.create_purchase(
        session, tenant_id=tenant_id, request=payload
    )
    # Idempotent replay → 200, fresh insert → 201 (per spec AC-3).
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return purchase


@router.get(
    "/purchases",
    response_model=list[PurchaseResponse],
    summary="List purchases reverse-derived from stock_movements.",
)
async def list_purchases(
    session: DbSession,
    tenant_id: TenantId,
    store_id: StoreIdQ = None,
    from_date: FromDateQ = None,
    to_date: ToDateQ = None,
    supplier_name: SupplierNameQ = None,
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> list[PurchaseResponse]:
    store_uuid = uuid.UUID(store_id) if store_id else None
    return await stock_service.list_purchases(
        session,
        tenant_id=tenant_id,
        store_id=store_uuid,
        from_date=from_date,
        to_date=to_date,
        supplier_name=supplier_name,
        limit=limit,
        offset=offset,
    )


# ──────────────────────────────────────────────────────────────────────────
# Adjustments
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/adjustments",
    response_model=AdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a 盤點 stock adjustment (one signed stock_movements row).",
)
async def create_adjustment(
    payload: AdjustmentCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
    response: Response,
) -> AdjustmentResponse:
    adj, created = await stock_service.create_adjustment(
        session, tenant_id=tenant_id, request=payload
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return adj


# ──────────────────────────────────────────────────────────────────────────
# Movements
# ──────────────────────────────────────────────────────────────────────────


@router.get(
    "/movements",
    response_model=list[StockMovementResponse],
    summary="List ledger rows. Supports filters and limit/offset pagination.",
)
async def list_movements(
    session: DbSession,
    tenant_id: TenantId,
    store_id: StoreIdQ = None,
    ingredient_id: IngredientIdQ = None,
    movement_type: MovementTypeQ = None,
    from_ts: FromTsQ = None,
    to_ts: ToTsQ = None,
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> list[StockMovementResponse]:
    store_uuid = uuid.UUID(store_id) if store_id else None
    ingredient_uuid = uuid.UUID(ingredient_id) if ingredient_id else None
    return await stock_service.list_movements(
        session,
        tenant_id=tenant_id,
        store_id=store_uuid,
        ingredient_id=ingredient_uuid,
        movement_type=movement_type,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        offset=offset,
    )


__all__ = ["router"]
