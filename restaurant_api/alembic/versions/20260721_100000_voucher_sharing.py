"""voucher sharing gifts and vip grants

Revision ID: b7e4a1c93d20
Revises: 41772db92af3
Create Date: 2026-07-21 10:00:00.000000+08:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4a1c93d20"
down_revision: str | Sequence[str] | None = "41772db92af3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 模式 A: voucher_gifts (member-to-friend shares) ──────────────────────
    op.create_table(
        "voucher_gifts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sender_id", sa.UUID(), nullable=False),
        sa.Column("recipient_id", sa.UUID(), nullable=True),
        sa.Column("voucher_id", sa.UUID(), nullable=True),
        sa.Column("share_token", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "CLAIMED",
                "REDEEMED",
                "EXPIRED",
                "REVOKED",
                name="voucher_gift_status",
                native_enum=False,
                length=16,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "reward_kind",
            sa.Enum(
                "POINTS",
                "CASH_CREDIT",
                name="voucher_reward_kind",
                native_enum=False,
                length=16,
            ),
            server_default="POINTS",
            nullable=False,
        ),
        sa.Column(
            "reward_amount",
            sa.Numeric(precision=14, scale=4),
            server_default="0",
            nullable=False,
        ),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["sender_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["voucher_id"], ["campaign_vouchers.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_voucher_gifts_tenant_token",
        "voucher_gifts",
        ["tenant_id", "share_token"],
        unique=True,
    )
    op.create_index(
        "ix_voucher_gifts_sender", "voucher_gifts", ["sender_id", "status"], unique=False
    )
    op.create_index(
        "ix_voucher_gifts_tenant_status",
        "voucher_gifts",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_gifts_sender_id"), "voucher_gifts", ["sender_id"], unique=False
    )
    op.create_index(
        op.f("ix_voucher_gifts_recipient_id"),
        "voucher_gifts",
        ["recipient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_gifts_voucher_id"),
        "voucher_gifts",
        ["voucher_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_gifts_tenant_id"), "voucher_gifts", ["tenant_id"], unique=False
    )

    # ── 模式 B: voucher_grants (VIP / 貴人 quota allotments) ──────────────────
    op.create_table(
        "voucher_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grantee_name", sa.String(length=120), nullable=False),
        sa.Column("grantee_id", sa.UUID(), nullable=True),
        sa.Column("voucher_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "face_value",
            sa.Numeric(precision=14, scale=4),
            server_default="0",
            nullable=False,
        ),
        sa.Column("menu_item_id", sa.UUID(), nullable=True),
        sa.Column("total_quota", sa.Integer(), nullable=False),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["grantee_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["menu_item_id"], ["menu_items.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_voucher_grants_tenant", "voucher_grants", ["tenant_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_voucher_grants_grantee_id"),
        "voucher_grants",
        ["grantee_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_grants_menu_item_id"),
        "voucher_grants",
        ["menu_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_grants_tenant_id"),
        "voucher_grants",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_voucher_grants_tenant_id"), table_name="voucher_grants")
    op.drop_index(op.f("ix_voucher_grants_menu_item_id"), table_name="voucher_grants")
    op.drop_index(op.f("ix_voucher_grants_grantee_id"), table_name="voucher_grants")
    op.drop_index("ix_voucher_grants_tenant", table_name="voucher_grants")
    op.drop_table("voucher_grants")

    op.drop_index(op.f("ix_voucher_gifts_tenant_id"), table_name="voucher_gifts")
    op.drop_index(op.f("ix_voucher_gifts_voucher_id"), table_name="voucher_gifts")
    op.drop_index(op.f("ix_voucher_gifts_recipient_id"), table_name="voucher_gifts")
    op.drop_index(op.f("ix_voucher_gifts_sender_id"), table_name="voucher_gifts")
    op.drop_index("ix_voucher_gifts_tenant_status", table_name="voucher_gifts")
    op.drop_index("ix_voucher_gifts_sender", table_name="voucher_gifts")
    op.drop_index("uq_voucher_gifts_tenant_token", table_name="voucher_gifts")
    op.drop_table("voucher_gifts")
