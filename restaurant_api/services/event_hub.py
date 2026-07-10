"""Real-time floor event transport (POS P1.5).

Two cooperating pieces:

1. ``record_event`` — called by services inside a request. It appends one
   append-only ``order_events`` row (atomic with the mutation: the event exists
   iff the transaction commits) AND stashes a JSON-serialisable copy on a
   request-scoped buffer.

2. ``EventHub`` — an in-process asyncio pub/sub keyed by ``store_id``. After the
   request's transaction commits (wired in ``api/deps.get_db``), the buffered
   events are drained and published to every WebSocket subscribed to that store.

Ordering / correctness contract:
  - The DB row is the durable source of truth; ``seq`` is the reconnect cursor.
  - We publish only *after commit*, so a rolled-back request never leaks a
    phantom event to tablets. If the process dies between commit and publish,
    the event is still in the table — a reconnecting tablet replays it via the
    ``seq`` cursor. No event is ever lost, at most it arrives late.

Scope note (single-process): the hub lives in one uvicorn process. With multiple
workers, each worker only pushes to its own sockets — a tablet on worker B won't
get worker A's live push, but its cursor-based reconnect/poll still converges.
Fan-out across workers (Redis pub/sub / Postgres LISTEN/NOTIFY) is a P2 upgrade;
the durable log + cursor already make it correct, just not instant cross-worker.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import OrderEvent

# Cap a single catch-up replay so a tablet that has been offline for days can't
# pull the whole history in one frame; it re-requests from the new cursor.
_CATCHUP_LIMIT = 500

logger = logging.getLogger("restaurant_api")

# Per-subscriber queue depth. If a tablet stalls and its queue fills, we drop
# the oldest live events for it (it will catch up via the seq cursor on its next
# reconnect / poll) rather than block every other subscriber.
_QUEUE_MAXSIZE = 256


class EventHub:
    """In-process fan-out of floor events to per-store WebSocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, store_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.setdefault(store_id, set()).add(q)
        return q

    def unsubscribe(self, store_id: uuid.UUID, q: asyncio.Queue[dict[str, Any]]) -> None:
        bucket = self._subscribers.get(store_id)
        if bucket is None:
            return
        bucket.discard(q)
        if not bucket:
            self._subscribers.pop(store_id, None)

    def subscriber_count(self, store_id: uuid.UUID) -> int:
        return len(self._subscribers.get(store_id, ()))

    def publish(self, store_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Fan a committed event out to every subscriber of ``store_id``.

        Non-blocking: a full subscriber queue drops its oldest event rather than
        stall the publisher. The subscriber recovers missed events via its seq
        cursor, so dropping a live push is safe (never a lost event, only a
        deferred one).
        """
        for q in self._subscribers.get(store_id, ()):  # copy-free read; set stable within loop
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # evict oldest
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.warning("event_hub.drop", extra={"store_id": str(store_id)})


# Process-wide singleton. WebSocket handlers and the after-commit publisher
# share this one instance.
_HUB = EventHub()


def get_hub() -> EventHub:
    return _HUB


# ── Request-scoped pending buffer ────────────────────────────────────────────
# Populated by record_event() during a request; drained + published by get_db
# after the transaction commits. ContextVar isolates it per request task.

_pending: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "order_event_pending", default=None
)


def begin_request_buffer() -> object:
    """Install a fresh pending buffer for this request context. Returns a token
    to hand back to ``reset_request_buffer`` in a finally."""
    return _pending.set([])


def reset_request_buffer(token: Any) -> None:
    _pending.reset(token)


def drain_pending() -> list[dict[str, Any]]:
    buf = _pending.get()
    if not buf:
        return []
    events = list(buf)
    buf.clear()
    return events


async def record_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    event_type: str,
    table_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one floor event row and buffer it for after-commit broadcast.

    Safe to call from anywhere with a session. If there is no request buffer in
    context (e.g. a background job), the durable row is still written — it just
    won't be live-pushed (a tablet picks it up on its next cursor poll).
    """
    row = OrderEvent(
        tenant_id=tenant_id,
        store_id=store_id,
        event_type=event_type,
        table_id=table_id,
        session_id=session_id,
        payload=payload or {},
    )
    session.add(row)
    await session.flush()
    # Pull the DB-assigned monotonic cursor so subscribers can advance past it.
    await session.refresh(row, ["seq", "created_at"])

    buf = _pending.get()
    if buf is not None:
        buf.append(serialize_event(row))


async def fetch_events_since(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    store_id: uuid.UUID,
    after_seq: int = 0,
    limit: int = _CATCHUP_LIMIT,
) -> list[dict[str, Any]]:
    """Durable catch-up: every event for a store with ``seq > after_seq``.

    Backs both the WS reconnect replay and the REST cursor endpoint (which is
    also the graceful-degradation path when a tablet can't hold a WebSocket).
    """
    rows = (
        await session.execute(
            select(OrderEvent)
            .where(
                OrderEvent.tenant_id == tenant_id,
                OrderEvent.store_id == store_id,
                OrderEvent.seq > after_seq,
            )
            .order_by(OrderEvent.seq)
            .limit(limit)
        )
    ).scalars().all()
    return [serialize_event(r) for r in rows]


def serialize_event(row: OrderEvent) -> dict[str, Any]:
    """JSON-safe wire shape shared by the WS stream and the REST cursor endpoint."""
    created = row.created_at or datetime.now(UTC)
    return {
        "seq": row.seq,
        "type": row.event_type,
        "store_id": str(row.store_id),
        "table_id": str(row.table_id) if row.table_id else None,
        "session_id": str(row.session_id) if row.session_id else None,
        "payload": row.payload or {},
        "at": created.astimezone(UTC).isoformat(),
    }


__all__ = [
    "EventHub",
    "begin_request_buffer",
    "drain_pending",
    "fetch_events_since",
    "get_hub",
    "record_event",
    "reset_request_buffer",
    "serialize_event",
]
