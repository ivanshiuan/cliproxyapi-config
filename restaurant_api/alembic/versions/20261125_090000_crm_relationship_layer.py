"""crm_relationship_layer

CRM relationship layer benchmarked on Twenty CRM (docs/14):

- ``customer_notes``        -- authored, timestamped notes per customer
- ``customer_tasks``        -- follow-up tasks (assignee / due / status)
- ``customer_tags``         -- tenant-defined free-form labels
- ``customer_tag_links``    -- customer <-> tag M2M
- ``customer_interactions`` -- logged touchpoints feeding the timeline

Revision ID: c4e9b2d71a05
Revises: a7c31f08d442
Create Date: 2026-11-25 09:00:00.000000+08:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e9b2d71a05"
down_revision: str | Sequence[str] | None = "a7c31f08d442"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts_cols() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # --- customer_notes ---------------------------------------------------
    op.create_table(
        "customer_notes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("author_employee_id", sa.UUID(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        *_ts_cols(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["author_employee_id"], ["employees.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_notes_tenant_id"), "customer_notes", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_customer_notes_customer_id"), "customer_notes", ["customer_id"]
    )
    op.create_index(
        op.f("ix_customer_notes_author_employee_id"),
        "customer_notes",
        ["author_employee_id"],
    )
    op.create_index(
        "ix_customer_notes_customer_time",
        "customer_notes",
        ["customer_id", "created_at"],
    )

    # --- customer_tasks ---------------------------------------------------
    op.create_table(
        "customer_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("assignee_employee_id", sa.UUID(), nullable=True),
        sa.Column("created_by_employee_id", sa.UUID(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="open"
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assignee_employee_id"], ["employees.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_employee_id"], ["employees.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_tasks_tenant_id"), "customer_tasks", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_customer_tasks_customer_id"), "customer_tasks", ["customer_id"]
    )
    op.create_index(
        op.f("ix_customer_tasks_assignee_employee_id"),
        "customer_tasks",
        ["assignee_employee_id"],
    )
    op.create_index(
        "ix_customer_tasks_customer_time",
        "customer_tasks",
        ["customer_id", "created_at"],
    )
    op.create_index(
        "ix_customer_tasks_assignee_status",
        "customer_tasks",
        ["assignee_employee_id", "status"],
    )
    op.create_index(
        "ix_customer_tasks_status_due", "customer_tasks", ["status", "due_at"]
    )

    # --- customer_tags ----------------------------------------------------
    op.create_table(
        "customer_tags",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        *_ts_cols(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customer_tags_tenant_id"), "customer_tags", ["tenant_id"])
    op.create_index(
        "uq_customer_tags_tenant_name_live",
        "customer_tags",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- customer_tag_links -----------------------------------------------
    op.create_table(
        "customer_tag_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["customer_tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_tag_links_tenant_id"), "customer_tag_links", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_customer_tag_links_customer_id"),
        "customer_tag_links",
        ["customer_id"],
    )
    op.create_index(
        op.f("ix_customer_tag_links_tag_id"), "customer_tag_links", ["tag_id"]
    )
    op.create_index(
        "uq_customer_tag_links_pair",
        "customer_tag_links",
        ["customer_id", "tag_id"],
        unique=True,
    )

    # --- customer_interactions --------------------------------------------
    op.create_table(
        "customer_interactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_interactions_tenant_id"),
        "customer_interactions",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_customer_interactions_customer_id"),
        "customer_interactions",
        ["customer_id"],
    )
    op.create_index(
        op.f("ix_customer_interactions_employee_id"),
        "customer_interactions",
        ["employee_id"],
    )
    op.create_index(
        "ix_customer_interactions_customer_time",
        "customer_interactions",
        ["customer_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("customer_interactions")
    op.drop_table("customer_tag_links")
    op.drop_index("uq_customer_tags_tenant_name_live", table_name="customer_tags")
    op.drop_table("customer_tags")
    op.drop_table("customer_tasks")
    op.drop_table("customer_notes")
