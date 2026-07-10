"""FastAPI router — ``/pos-auth`` (店員 POS 登入 + PIN 管理).

- ``POST /pos-auth/set-pin`` — 店長後台設定/換發員工 PIN(需 admin session)。
- ``POST /pos-auth/login``   — 員工用 PIN 登入 → 回短效 POS token。
- ``GET  /pos-auth/me``      — 回目前登入者身分 + 權限(UI 載入用)。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from ..api.auth import Admin
from ..api.deps import DbSession, TenantId
from ..api.errors import AuthenticationError
from ..api.pos_auth import PosActor, make_pos_token
from ..config import get_settings
from ..schemas.pos_auth import (
    PosEmployeeOption,
    PosLoginRequest,
    PosLoginResponse,
    PosMe,
    SetPinRequest,
)
from ..services import pos_auth_service
from ..services.permissions import permissions_for

_Q_STORE_ID_REQ = Query()

router = APIRouter(prefix="/pos-auth", tags=["pos-auth"])


@router.get(
    "/employees",
    response_model=list[PosEmployeeOption],
    summary="門市員工清單 (登入前選人用;不含祕密)",
)
async def list_employees(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID = _Q_STORE_ID_REQ,
) -> list[PosEmployeeOption]:
    rows = await pos_auth_service.list_employees(
        session, tenant_id=tenant_id, store_id=store_id
    )
    return [
        PosEmployeeOption(
            id=e.id, full_name=e.full_name, role=e.role, has_pin=e.pin_hash is not None
        )
        for e in rows
    ]


@router.post("/set-pin", summary="設定/換發員工 PIN (需店長後台登入)")
async def set_pin(
    payload: SetPinRequest,
    session: DbSession,
    tenant_id: TenantId,
    _: Admin,
) -> dict[str, bool]:
    await pos_auth_service.set_pin(
        session, tenant_id=tenant_id, employee_id=payload.employee_id, pin=payload.pin
    )
    return {"ok": True}


@router.post("/login", response_model=PosLoginResponse, summary="員工 PIN 登入")
async def login(
    payload: PosLoginRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> PosLoginResponse:
    principal, full_name = await pos_auth_service.login(
        session, tenant_id=tenant_id, employee_id=payload.employee_id, pin=payload.pin
    )
    token = make_pos_token(principal, get_settings())
    return PosLoginResponse(
        token=token,
        employee_id=principal.employee_id,
        full_name=full_name,
        role=principal.role,
        permissions=permissions_for(principal.role),
    )


@router.get("/me", response_model=PosMe, summary="目前登入者身分 + 權限")
async def me(
    session: DbSession,
    tenant_id: TenantId,
    principal: PosActor,
) -> PosMe:
    if principal is None:
        raise AuthenticationError("尚未登入 POS")
    emp = await pos_auth_service._load_employee(
        session, principal.employee_id, tenant_id
    )
    return PosMe(
        employee_id=principal.employee_id,
        full_name=emp.full_name,
        role=principal.role,
        store_id=principal.store_id,
        permissions=permissions_for(principal.role),
    )


__all__ = ["router"]
