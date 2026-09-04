"""one OPEN order per table session — DB-level race guard

Revision ID: b7d2e9a41f05
Revises: 94c59eb86478
Create Date: 2026-07-18 16:15:00.000000+08:00

``_get_or_create_session_order`` is called from *every* read/write path that
touches a table session's bill (GET order, GET quote, POST lines, table-QR).
Two concurrent first-touch requests (e.g. the POS front-end fetching order and
quote in parallel right after opening a table, or a clerk's tablet racing a
customer's table-QR phone) each saw "no open order yet" in their own
transaction and both inserted one → two OPEN orders on the same session →
every later call died with ``MultipleResultsFound``.

This partial unique index makes the invariant "a session has at most one open
order" a database guarantee; the service catches the unique violation and
adopts the winner's row (see ``pos_order_service._get_or_create_session_order``).

NOTE ``native_enum=False`` stores the enum **NAME** — the predicate must
compare ``'OPEN'``/(not ``'open'``) or the index silently never applies
(documented pitfall, pinned by tests/test_tables_model.py).

Pre-existing duplicates (only possible via the race this fixes): keep the
oldest order per session, mark later *empty* duplicates VOIDED; duplicates
that already carry lines/payments abort the migration for manual merge.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e9a41f05"
down_revision: str | Sequence[str] | None = "94c59eb86478"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dedupe survivors of the race before the index can be built. Later
    # duplicates are empty shells (the crash happened before any line landed);
    # anything non-empty is unexpected → fail loudly instead of guessing.
    op.execute(
        """
        DO $$
        DECLARE bad integer;
        BEGIN
            SELECT count(*) INTO bad
            FROM orders o
            WHERE o.status = 'OPEN' AND o.deleted_at IS NULL
              AND o.table_session_id IS NOT NULL
              AND EXISTS (
                    SELECT 1 FROM orders k
                    WHERE k.table_session_id = o.table_session_id
                      AND k.status = 'OPEN' AND k.deleted_at IS NULL
                      AND k.created_at < o.created_at)
              AND (EXISTS (SELECT 1 FROM order_lines l WHERE l.order_id = o.id)
                   OR EXISTS (SELECT 1 FROM order_payments p WHERE p.order_id = o.id));
            IF bad > 0 THEN
                RAISE EXCEPTION
                    'found % duplicate OPEN orders with lines/payments — merge manually first',
                    bad;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE orders o SET status = 'VOIDED', updated_at = now()
        WHERE o.status = 'OPEN' AND o.deleted_at IS NULL
          AND o.table_session_id IS NOT NULL
          AND EXISTS (
                SELECT 1 FROM orders k
                WHERE k.table_session_id = o.table_session_id
                  AND k.status = 'OPEN' AND k.deleted_at IS NULL
                  AND k.created_at < o.created_at)
        """
    )
    op.create_index(
        "uq_orders_one_open_per_session",
        "orders",
        ["table_session_id"],
        unique=True,
        postgresql_where="status = 'OPEN' AND deleted_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_orders_one_open_per_session", table_name="orders")
