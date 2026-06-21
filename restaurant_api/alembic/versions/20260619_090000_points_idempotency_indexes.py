"""points ledger idempotency backstops for membership-lifecycle grants

Revision ID: a1c4f7e2b9d3
Revises: 485b908ca19c
Create Date: 2026-06-19 09:00:00.000000+08:00

Adds two partial unique indexes on ``customer_points_ledger`` so the membership
lifecycle grants (welcome / birthday / dormant win-back) are idempotent at the
database level — "grant once" holds even under concurrent or multi-instance job
runs, not just sequential re-runs. Partial predicates keep the high-volume
reasons (order.earn / redeem.coupon / expire.batch) unconstrained.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4f7e2b9d3"
down_revision: str | Sequence[str] | None = "485b908ca19c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_points_welcome_once",
        "customer_points_ledger",
        ["customer_id"],
        unique=True,
        postgresql_where="reason = 'welcome.signup'",
    )
    op.create_index(
        "uq_points_periodic_marker",
        "customer_points_ledger",
        ["customer_id", "reason", "note"],
        unique=True,
        postgresql_where="reason IN ('birthday.gift', 'winback.dormant')",
    )


def downgrade() -> None:
    op.drop_index("uq_points_periodic_marker", table_name="customer_points_ledger")
    op.drop_index("uq_points_welcome_once", table_name="customer_points_ledger")
