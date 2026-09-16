"""Floor real-time event log — the append-only stream that drives multi-tablet sync.

Every table/order state change (open桌 / 結桌 / 轉桌 / 加點 / 退菜 / 結帳) appends
ONE row here. Tablets subscribe over a WebSocket (``/tables/ws``) and, on each
event, re-fetch the authoritative floor plan / order — the event is a "wake up
and refetch" signal plus a monotonic cursor, never the source of truth for
display state (which avoids the drift bug class of pushing full state blobs).

Why a dedicated table (not reuse ``audit_log``):
  - ``audit_log`` is the compliance/forensics ledger — every business mutation,
    heavy payloads, not ordered for streaming.
  - ``order_events`` is the *UI transport* — floor scope only, a strict BIGINT
    ``seq`` cursor so a tablet that reconnects can replay exactly what it missed
    with ``WHERE store_id = ? AND seq > last_event_id``.

Append-only, like the other ledgers: a Postgres ``INSTEAD NOTHING`` rule (added
in the migration) drops any UPDATE/DELETE so history can't be rewritten.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TenantScopedMixin, uuid7


class OrderEvent(TenantScopedMixin, Base):
    """One append-only floor event. No ``updated_at`` / ``deleted_at`` — immutable.

    ``seq`` is a store-agnostic monotonic counter (Postgres IDENTITY) used as the
    reconnect cursor; ``id`` stays a UUIDv7 PK per the house rule. ``table_id`` /
    ``session_id`` let a tablet cheaply decide *what* to refetch without parsing
    ``payload``.
    """

    __tablename__ = "order_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # Monotonic global cursor. IDENTITY so the DB owns assignment; clients only
    # ever compare it (``seq > last_event_id``), never generate it.
    seq: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Dotted event name, e.g. "session.opened", "order.line_voided", "order.checkout".
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    # Small JSON body — ids + minimal display hints. NOT authoritative state.
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # The catch-up scan: "everything this store did after my cursor".
        Index("ix_order_events_store_seq", "store_id", "seq"),
    )


__all__ = ["OrderEvent"]
