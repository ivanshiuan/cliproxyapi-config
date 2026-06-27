"""auth_rbac_seed — system permissions + 5 system roles + grants.

See specs/auth_rbac_system.md §Permission catalog + §Role→permissions mapping.

Idempotent: re-running this migration on a partially-seeded DB only
inserts rows that don't already exist (ON CONFLICT DO NOTHING). This
matters because PR-E's bootstrap CLI may have already touched some of
these tables; we don't want a hard failure on re-deploy.

System rows are marked with ``is_system=true`` so the (future) admin UI
knows not to expose delete buttons on them.

Revision ID: b5c8d3e1f0aa
Revises: a4b7c1d2e3f9
Create Date: 2026-06-27 15:31:00.000000+08:00
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c8d3e1f0aa"
down_revision: str | Sequence[str] | None = "a4b7c1d2e3f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mirror of restaurant_api.models.base.uuid7() — duplicated here so the
# migration has no runtime dependency on the application package, which
# matters when Alembic runs under different conditions (CI, prod deploy,
# etc.) and the package import path may not be guaranteed.
def _uuid7() -> uuid.UUID:
    import os

    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 64) & 0x0FFF
    rand_b = rand & 0x3FFFFFFFFFFFFFFF
    value = (unix_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


# ---------------------------------------------------------------------------
# Catalog (kept in code; the canonical version is in
# specs/auth_rbac_system.md — keep in sync on every spec edit).
# ---------------------------------------------------------------------------

PERMISSIONS: list[tuple[str, str]] = [
    # orders
    ("orders:create", "Create new orders"),
    ("orders:read", "Read orders within own tenant"),
    ("orders:close", "Close (settle) an order"),
    ("orders:void", "Void an order"),
    ("orders:refund", "Issue refund on closed order (reserved)"),
    # stock
    ("stock:intake", "Record purchase / intake into stock"),
    ("stock:read", "Read stock movements & on-hand"),
    ("stock:adjust", "Manual stock adjustment (loss / found / count)"),
    # clock
    ("clock:read_self", "Read own clock-in/out records"),
    ("clock:read_store", "Read clock records of entire store"),
    ("clock:admin", "Edit / correct clock records (manager)"),
    # cost
    ("cost:create", "Create cost events (utility / rent / etc)"),
    ("cost:read", "Read cost events"),
    # visual assets
    ("visual_assets:read_brand_ref", "Read brand reference assets"),
    ("visual_assets:write", "Upload / edit visual assets"),
    # events
    ("events:emit", "Emit domain events"),
    # auth admin
    ("auth:manage_users", "Create / disable user_credentials"),
    ("auth:manage_roles", "Grant / revoke roles to employees"),
    ("auth:reset_password", "Force reset another user's password"),
    # tenant admin
    ("admin:tenant_settings", "Edit tenant-level settings"),
    ("admin:audit_read", "Read audit_log"),
    ("admin:export_data", "Export tenant data dump"),
]

ROLES: list[tuple[str, str, str]] = [
    # (name, display_name, description)
    (
        "owner",
        "Owner",
        "Full access to the tenant — typically the legal owner / founder.",
    ),
    (
        "store_manager",
        "Store Manager",
        "Operational role: orders / stock / clock / cost / events / audit.",
    ),
    (
        "marketing",
        "Marketing",
        "Manages visual assets and customer-facing content.",
    ),
    (
        "supplier",
        "Supplier",
        "Read-only access to stock movements scoped to own deliveries.",
    ),
    (
        "staff",
        "Staff",
        "Front-of-house: take/close orders, see own clock records.",
    ),
]

# Role → permissions (by permission name).
ROLE_PERMS: dict[str, list[str]] = {
    "owner": [name for name, _ in PERMISSIONS],
    "store_manager": [
        "orders:create",
        "orders:read",
        "orders:close",
        "orders:void",
        "orders:refund",
        "stock:intake",
        "stock:read",
        "stock:adjust",
        "clock:read_self",
        "clock:read_store",
        "clock:admin",
        "cost:create",
        "cost:read",
        "events:emit",
        "visual_assets:read_brand_ref",
        "admin:audit_read",
    ],
    "marketing": [
        "orders:read",
        "visual_assets:read_brand_ref",
        "visual_assets:write",
        "events:emit",
    ],
    "supplier": [
        "stock:read",
    ],
    "staff": [
        "orders:create",
        "orders:read",
        "orders:close",
        "clock:read_self",
    ],
}


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # permissions — insert and capture id by name for later joins.
    # ------------------------------------------------------------------
    perm_id_by_name: dict[str, uuid.UUID] = {}
    for name, description in PERMISSIONS:
        new_id = _uuid7()
        result = conn.execute(
            sa.text(
                """
                INSERT INTO permissions (id, name, description, is_system)
                VALUES (:id, :name, :description, true)
                ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
                RETURNING id
                """
            ),
            {"id": new_id, "name": name, "description": description},
        )
        perm_id_by_name[name] = result.scalar_one()

    # ------------------------------------------------------------------
    # roles — system roles, tenant_id NULL.
    # ------------------------------------------------------------------
    role_id_by_name: dict[str, uuid.UUID] = {}
    for name, display_name, description in ROLES:
        new_id = _uuid7()
        # tenant_id IS NULL → the UNIQUE (tenant_id, name) lets us seed
        # system roles once. ON CONFLICT relies on a partial unique
        # index, which we don't have, so we do an existence check first.
        existing = conn.execute(
            sa.text(
                "SELECT id FROM roles WHERE name = :name AND tenant_id IS NULL"
            ),
            {"name": name},
        ).scalar()
        if existing is not None:
            role_id_by_name[name] = existing
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO roles
                    (id, name, display_name, description, is_system, tenant_id)
                VALUES
                    (:id, :name, :display_name, :description, true, NULL)
                """
            ),
            {
                "id": new_id,
                "name": name,
                "display_name": display_name,
                "description": description,
            },
        )
        role_id_by_name[name] = new_id

    # ------------------------------------------------------------------
    # role_permissions — M:N grants.
    # ------------------------------------------------------------------
    for role_name, perm_names in ROLE_PERMS.items():
        role_id = role_id_by_name[role_name]
        for perm_name in perm_names:
            perm_id = perm_id_by_name[perm_name]
            conn.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (id, role_id, permission_id)
                    VALUES (:id, :role_id, :permission_id)
                    ON CONFLICT (role_id, permission_id) DO NOTHING
                    """
                ),
                {
                    "id": _uuid7(),
                    "role_id": role_id,
                    "permission_id": perm_id,
                },
            )


def downgrade() -> None:
    conn = op.get_bind()
    perm_names = [name for name, _ in PERMISSIONS]
    role_names = [name for name, _, _ in ROLES]

    # role_permissions cascade with role / permission delete, but we
    # drop them explicitly for clarity and to avoid surprising other
    # rows the bootstrap CLI may have added.
    conn.execute(
        sa.text(
            """
            DELETE FROM role_permissions
             WHERE role_id IN (
                 SELECT id FROM roles
                  WHERE name = ANY(:names) AND tenant_id IS NULL
             )
            """
        ),
        {"names": role_names},
    )
    conn.execute(
        sa.text(
            "DELETE FROM roles WHERE name = ANY(:names) AND tenant_id IS NULL"
        ),
        {"names": role_names},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE name = ANY(:names)"),
        {"names": perm_names},
    )
