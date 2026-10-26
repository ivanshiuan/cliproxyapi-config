"""Employee CRUD (admin console).

Manages staff master records: create, list, update, soft-delete.
PIN management stays in pos_auth router (requires its own auth flow).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from ..api.deps import DbSession, TenantId
from ..api.errors import NotFoundError
from ..models import Employee, EmployeeRole

router = APIRouter(prefix="/employees", tags=["employees"])


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    full_name: str = Field(min_length=1, max_length=120)
    role: EmployeeRole
    store_id: uuid.UUID | None = None
    hourly_wage: Decimal = Field(ge=Decimal("0"))
    monthly_salary: Decimal | None = Field(default=None, ge=Decimal("0"))
    hired_on: date
    is_active: bool = True


class EmployeeUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: EmployeeRole | None = None
    store_id: uuid.UUID | None = None
    hourly_wage: Decimal | None = Field(default=None, ge=Decimal("0"))
    monthly_salary: Decimal | None = Field(default=None, ge=Decimal("0"))
    hired_on: date | None = None
    terminated_on: date | None = None
    is_active: bool | None = None


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    store_id: uuid.UUID | None
    full_name: str
    role: EmployeeRole
    hourly_wage: Decimal
    monthly_salary: Decimal | None
    hired_on: date
    terminated_on: date | None
    is_active: bool
    has_pin: bool


def _to_out(e: Employee) -> EmployeeOut:
    return EmployeeOut(
        id=e.id,
        store_id=e.store_id,
        full_name=e.full_name,
        role=e.role,
        hourly_wage=e.hourly_wage,
        monthly_salary=e.monthly_salary,
        hired_on=e.hired_on,
        terminated_on=e.terminated_on,
        is_active=e.is_active,
        has_pin=e.pin_hash is not None,
    )


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    session: DbSession,
    tenant_id: TenantId,
    store_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[EmployeeOut]:
    q = select(Employee).where(
        Employee.tenant_id == tenant_id,
        Employee.deleted_at.is_(None),
    )
    if store_id:
        q = q.where(Employee.store_id == store_id)
    if not include_inactive:
        q = q.where(Employee.is_active.is_(True))
    q = q.order_by(Employee.full_name)
    rows = (await session.execute(q)).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(
    payload: EmployeeCreate,
    session: DbSession,
    tenant_id: TenantId,
) -> EmployeeOut:
    row = Employee(
        tenant_id=tenant_id,
        store_id=payload.store_id,
        full_name=payload.full_name,
        role=payload.role,
        hourly_wage=payload.hourly_wage,
        monthly_salary=payload.monthly_salary,
        hired_on=payload.hired_on,
        is_active=payload.is_active,
    )
    session.add(row)
    await session.flush()
    return _to_out(row)


@router.patch("/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: uuid.UUID,
    payload: EmployeeUpdate,
    session: DbSession,
    tenant_id: TenantId,
) -> EmployeeOut:
    row = (
        await session.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.tenant_id == tenant_id,
                Employee.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("employee not found", details={"employee_id": str(employee_id)})
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, val)
    await session.flush()
    return _to_out(row)


@router.delete("/{employee_id}", status_code=204)
async def delete_employee(
    employee_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    row = (
        await session.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.tenant_id == tenant_id,
                Employee.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("employee not found", details={"employee_id": str(employee_id)})
    row.deleted_at = func.now()
    row.is_active = False
    await session.flush()
