"""pos_parity_tables_modifiers_order_channel

好點 POS gap-fill (docs/13_haodian_pos_comparison.md G2-G6):

- ``dining_tables`` -- 桌位管理
- ``modifier_groups`` / ``modifier_options`` / ``menu_item_modifier_groups``
  -- 商品客製群組 (甜度/冰量/加料)
- ``orders`` add ``service_type`` (內用/外帶/外送), ``order_source``
  (POS/QR/LINE/delivery), ``table_id``, ``refunded_at``, ``refund_reason``
- ``order_lines`` add ``modifiers`` JSONB snapshot

Revision ID: a7c31f08d442
Revises: 41772db92af3
Create Date: 2026-07-23 10:15:00.000000+08:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a7c31f08d442"
down_revision: str | Sequence[str] | None = "41772db92af3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- dining_tables ----------------------------------------------------
    op.create_table(
        "dining_tables",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("zone", sa.String(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dining_tables_tenant_id"), "dining_tables", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_dining_tables_store_id"), "dining_tables", ["store_id"], unique=False
    )
    op.create_index(
        "uq_dining_tables_store_name_live",
        "dining_tables",
        ["store_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- modifier_groups --------------------------------------------------
    op.create_table(
        "modifier_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "selection_type",
            sa.Enum("single", "multi", name="modifier_selection_type"),
            nullable=False,
            server_default="single",
        ),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("min_select", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_select", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_modifier_groups_tenant_id"),
        "modifier_groups",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_modifier_groups_store_id"),
        "modifier_groups",
        ["store_id"],
        unique=False,
    )

    # --- modifier_options -------------------------------------------------
    op.create_table(
        "modifier_options",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "price_delta", sa.Numeric(14, 4), nullable=False, server_default="0"
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["modifier_groups.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_modifier_options_tenant_id"),
        "modifier_options",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_modifier_options_group_id"),
        "modifier_options",
        ["group_id"],
        unique=False,
    )

    # --- menu_item_modifier_groups ---------------------------------------
    op.create_table(
        "menu_item_modifier_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("menu_item_id", sa.UUID(), nullable=False),
        sa.Column("modifier_group_id", sa.UUID(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["menu_item_id"], ["menu_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["modifier_group_id"], ["modifier_groups.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_menu_item_modifier_groups_tenant_id"),
        "menu_item_modifier_groups",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_menu_item_modifier_groups_menu_item_id"),
        "menu_item_modifier_groups",
        ["menu_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_menu_item_modifier_groups_modifier_group_id"),
        "menu_item_modifier_groups",
        ["modifier_group_id"],
        unique=False,
    )
    op.create_index(
        "uq_menu_item_modifier_groups_pair",
        "menu_item_modifier_groups",
        ["menu_item_id", "modifier_group_id"],
        unique=True,
    )

    # --- orders: channel / table / refund columns ------------------------
    op.add_column(
        "orders",
        sa.Column(
            "service_type",
            sa.String(length=16),
            nullable=False,
            server_default="dine_in",
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "order_source",
            sa.String(length=16),
            nullable=False,
            server_default="pos",
        ),
    )
    op.add_column("orders", sa.Column("table_id", sa.UUID(), nullable=True))
    op.add_column(
        "orders", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("orders", sa.Column("refund_reason", sa.Text(), nullable=True))
    op.create_index(op.f("ix_orders_table_id"), "orders", ["table_id"], unique=False)
    op.create_foreign_key(
        "orders_table_id_fkey",
        "orders",
        "dining_tables",
        ["table_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- order_lines: modifier snapshot ----------------------------------
    op.add_column(
        "order_lines",
        sa.Column("modifiers", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("order_lines", "modifiers")

    op.drop_constraint("orders_table_id_fkey", "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_table_id"), table_name="orders")
    op.drop_column("orders", "refund_reason")
    op.drop_column("orders", "refunded_at")
    op.drop_column("orders", "table_id")
    op.drop_column("orders", "order_source")
    op.drop_column("orders", "service_type")

    op.drop_index(
        "uq_menu_item_modifier_groups_pair", table_name="menu_item_modifier_groups"
    )
    op.drop_index(
        op.f("ix_menu_item_modifier_groups_modifier_group_id"),
        table_name="menu_item_modifier_groups",
    )
    op.drop_index(
        op.f("ix_menu_item_modifier_groups_menu_item_id"),
        table_name="menu_item_modifier_groups",
    )
    op.drop_index(
        op.f("ix_menu_item_modifier_groups_tenant_id"),
        table_name="menu_item_modifier_groups",
    )
    op.drop_table("menu_item_modifier_groups")

    op.drop_index(op.f("ix_modifier_options_group_id"), table_name="modifier_options")
    op.drop_index(op.f("ix_modifier_options_tenant_id"), table_name="modifier_options")
    op.drop_table("modifier_options")

    op.drop_index(op.f("ix_modifier_groups_store_id"), table_name="modifier_groups")
    op.drop_index(op.f("ix_modifier_groups_tenant_id"), table_name="modifier_groups")
    op.drop_table("modifier_groups")
    sa.Enum(name="modifier_selection_type").drop(op.get_bind(), checkfirst=True)

    op.drop_index("uq_dining_tables_store_name_live", table_name="dining_tables")
    op.drop_index(op.f("ix_dining_tables_store_id"), table_name="dining_tables")
    op.drop_index(op.f("ix_dining_tables_tenant_id"), table_name="dining_tables")
    op.drop_table("dining_tables")
