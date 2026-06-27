"""auth_rbac_schema — 6 new tables for Phase 2 authentication & RBAC.

See specs/auth_rbac_system.md PR-A for the full design rationale.
This is purely schema (DDL) — no data seed; that lives in the
follow-up migration ``20260627_153100_auth_rbac_seed``.

Tables created:
    - user_credentials      1:1 with employees, login email + argon2id hash
    - roles                 functional roles (tenant-scoped + system)
    - permissions           resource:action catalog
    - role_permissions      M:N grants
    - employee_roles        M:N grants with audit (granted_by/granted_at)
    - refresh_tokens        server-side state for revocable refresh tokens

Extensions required: ``citext`` (case-insensitive email column).

Revision ID: a4b7c1d2e3f9
Revises: 41772db92af3
Create Date: 2026-06-27 15:30:00.000000+08:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4b7c1d2e3f9"
down_revision: str | Sequence[str] | None = "41772db92af3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Required for case-insensitive email column.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ------------------------------------------------------------------
    # user_credentials
    # ------------------------------------------------------------------
    op.create_table(
        "user_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", name="uq_user_credentials_employee"),
        sa.UniqueConstraint("email", name="uq_user_credentials_email"),
    )

    # ------------------------------------------------------------------
    # roles
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
        sa.CheckConstraint(
            "name ~ '^[a-z][a-z0-9_]*$'",
            name="ck_roles_name_snake_case",
        ),
    )

    # ------------------------------------------------------------------
    # permissions
    # ------------------------------------------------------------------
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_permissions_name"),
        sa.CheckConstraint(
            "name ~ '^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$'",
            name="ck_permissions_name_resource_action",
        ),
    )

    # ------------------------------------------------------------------
    # role_permissions
    # ------------------------------------------------------------------
    op.create_table(
        "role_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "permission_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permissions_pair"
        ),
    )

    # ------------------------------------------------------------------
    # employee_roles
    # ------------------------------------------------------------------
    op.create_table(
        "employee_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "employee_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["employees.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "employee_id", "role_id", name="uq_employee_roles_pair"
        ),
    )
    op.create_index(
        "ix_employee_roles_employee_id",
        "employee_roles",
        ["employee_id"],
        unique=False,
    )
    op.create_index(
        "ix_employee_roles_role_id",
        "employee_roles",
        ["role_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # refresh_tokens
    # ------------------------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "employee_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_from_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_hash"),
    )
    op.create_index(
        "ix_refresh_tokens_employee_active",
        "refresh_tokens",
        ["employee_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_refresh_tokens_employee_active", table_name="refresh_tokens"
    )
    op.drop_table("refresh_tokens")
    op.drop_index("ix_employee_roles_role_id", table_name="employee_roles")
    op.drop_index("ix_employee_roles_employee_id", table_name="employee_roles")
    op.drop_table("employee_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("user_credentials")
    # citext extension is left in place — other tables may use it later.
