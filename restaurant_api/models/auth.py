"""Auth & RBAC — credentials, functional roles, permissions, refresh tokens.

This module backs the Auth/RBAC system spec (`specs/auth_rbac_system.md`).
The design treats employees.role (HR contractual role: owner/manager/staff)
as orthogonal to functional roles defined here (marketing/supplier/...),
so a single Employee row can be hire-as-staff but granted-as-marketing.

Tables:
    - user_credentials   1:1 employees, login email + argon2id password
    - roles              functional roles (tenant-scoped or system-wide)
    - permissions        ``resource:action`` strings (system-managed catalog)
    - role_permissions   M:N grants
    - employee_roles     M:N grants (with audit on who granted when)
    - refresh_tokens     server-side state for revocable refresh path

The refresh_tokens table is the only non-ledger auth table allowed to
UPDATE (we mark revoked_at); DELETE is forbidden so forensics can trace
a compromised session even after expiry.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampedMixin, uuid7


class UserCredential(TimestampedMixin, Base):
    """Login credentials for an employee.

    1:1 with ``employees`` — separated from the employee record because
    HR data (wage, role enum, hired_on) and auth data (password hash,
    lockout state) move on different lifecycles. An employee may also
    exist without credentials (e.g. a part-timer who never logs in).
    """

    __tablename__ = "user_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    employee = relationship(
        "Employee", back_populates="credential", uselist=False
    )


class Role(TimestampedMixin, Base):
    """Functional role — orthogonal to ``Employee.role`` (HR enum).

    System roles (``is_system=True``, ``tenant_id IS NULL``) are seeded
    at install and cannot be deleted. Tenant-scoped roles let chains
    define brand-specific buckets later without colliding on name.
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
        CheckConstraint(
            "name ~ '^[a-z][a-z0-9_]*$'", name="ck_roles_name_snake_case"
        ),
    )


class Permission(TimestampedMixin, Base):
    """``resource:action`` permission string. Catalog is system-managed.

    Format enforced by CHECK constraint: lowercase snake_case for both
    halves, separated by a single colon. Examples:
        orders:create, visual_assets:read_brand_ref, auth:grant_role.
    """

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    __table_args__ = (
        CheckConstraint(
            "name ~ '^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$'",
            name="ck_permissions_name_resource_action",
        ),
    )


class RolePermission(Base):
    """M:N — which permissions a role grants. CASCADE on both sides."""

    __tablename__ = "role_permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permissions_pair"
        ),
    )


class EmployeeRoleGrant(Base):
    """M:N — which functional roles an employee holds.

    Class is named ``EmployeeRoleGrant`` (not ``EmployeeRole``) because
    ``EmployeeRole`` already exists as the HR enum on ``Employee.role``;
    keeping it distinct prevents import collisions. The DB table is still
    ``employee_roles`` per spec.

    RESTRICT on role delete (don't let a role disappear from under
    employees); CASCADE on employee delete (cleanup grants when an
    employee row is hard-deleted, though SoftDeleteMixin means we
    normally just flip deleted_at and leave grants intact).
    ``granted_by=NULL`` denotes a system grant (e.g. bootstrap CLI).
    """

    __tablename__ = "employee_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "role_id", name="uq_employee_roles_pair"
        ),
        Index("ix_employee_roles_employee_id", "employee_id"),
        Index("ix_employee_roles_role_id", "role_id"),
    )


class RefreshToken(Base):
    """Server-side state for the JWT refresh path.

    Refresh tokens are opaque (random 32-byte url-safe string sent to the
    client); only the sha256 hex is stored here. UPDATE allowed for
    ``revoked_at``; DELETE forbidden by convention (forensics retention).
    A cleanup job may hard-delete rows where ``expires_at < now() - 90d``.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(
        CHAR(64), nullable=False, unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_from_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_refresh_tokens_employee_active",
            "employee_id",
            postgresql_where="revoked_at IS NULL",
        ),
    )


__all__ = [
    "EmployeeRoleGrant",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "UserCredential",
]
