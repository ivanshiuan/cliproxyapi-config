"""Employee — staff master record, also keyed by the HR module."""

from __future__ import annotations

import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    Money,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class EmployeeRole(enum.StrEnum):
    """High-level role buckets used for scheduling + permissions.

    The schema doc uses freeform text; we tighten that into an enum here
    since the FastAPI layer also wants a closed set for validation.
    """

    OWNER = "owner"
    MANAGER = "manager"
    CHEF = "chef"
    STAFF = "staff"
    PARTTIME = "parttime"


class Employee(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A person who works for a tenant.

    ``store_id`` is nullable to model cross-store roles (e.g. an owner who
    floats between stores, or HQ staff in a future chain rollout). For a
    single-store tenant this column is just always set to the one store.
    """

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # SET NULL is safe here: if a store is removed, employee record persists
    # without a primary store assignment.
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[EmployeeRole] = mapped_column(
        SQLEnum(EmployeeRole, name="employee_role", native_enum=False, length=32),
        nullable=False,
    )
    # POS PIN credential (PBKDF2 hash via services/pin_security). Nullable:
    # an employee without a PIN simply can't log in to the POS yet — the boss
    # sets it from the admin console. Never stores the raw PIN.
    pin_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    hourly_wage: Mapped[Decimal] = mapped_column(Money, nullable=False)
    monthly_salary: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    hired_on: Mapped[date] = mapped_column(Date, nullable=False)
    terminated_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    __table_args__ = (
        Index("ix_employees_tenant_store", "tenant_id", "store_id"),
    )


__all__ = ["Employee", "EmployeeRole"]
