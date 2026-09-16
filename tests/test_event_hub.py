"""Unit tests for the in-process floor EventHub (P1.5).

Pure asyncio — no DB. Covers fan-out, per-store isolation, unsubscribe cleanup,
and the drop-oldest overflow policy that keeps one slow tablet from stalling the
publisher (the dropped event is recovered later via the seq cursor).
"""

from __future__ import annotations

import uuid

import pytest

from restaurant_api.services.event_hub import EventHub

pytestmark = pytest.mark.asyncio


async def test_publish_reaches_every_subscriber_of_that_store() -> None:
    hub = EventHub()
    store = uuid.uuid4()
    a = hub.subscribe(store)
    b = hub.subscribe(store)

    hub.publish(store, {"seq": 1, "type": "session.opened"})

    assert (await a.get())["seq"] == 1
    assert (await b.get())["seq"] == 1


async def test_publish_is_isolated_per_store() -> None:
    hub = EventHub()
    store_a, store_b = uuid.uuid4(), uuid.uuid4()
    qa = hub.subscribe(store_a)
    qb = hub.subscribe(store_b)

    hub.publish(store_a, {"seq": 7})

    assert qa.qsize() == 1
    assert qb.qsize() == 0  # a different store never sees it


async def test_publish_to_store_with_no_subscribers_is_a_noop() -> None:
    hub = EventHub()
    # Must not raise even though nobody is listening.
    hub.publish(uuid.uuid4(), {"seq": 1})


async def test_unsubscribe_stops_delivery_and_cleans_up() -> None:
    hub = EventHub()
    store = uuid.uuid4()
    q = hub.subscribe(store)
    assert hub.subscriber_count(store) == 1

    hub.unsubscribe(store, q)
    assert hub.subscriber_count(store) == 0

    hub.publish(store, {"seq": 1})
    assert q.qsize() == 0  # unsubscribed queue receives nothing


async def test_full_queue_drops_oldest_not_newest() -> None:
    """A stalled subscriber must not block the publisher; we evict the oldest
    buffered event (the tablet recovers it via its seq cursor on reconnect)."""
    hub = EventHub()
    store = uuid.uuid4()
    q = hub.subscribe(store)

    # Fill well past the queue cap.
    from restaurant_api.services.event_hub import _QUEUE_MAXSIZE

    total = _QUEUE_MAXSIZE + 50
    for seq in range(total):
        hub.publish(store, {"seq": seq})

    # Queue never exceeds its cap, and it retained the NEWEST events (oldest
    # were evicted) — so a live subscriber always sees the latest state.
    assert q.qsize() == _QUEUE_MAXSIZE
    seqs = [q.get_nowait()["seq"] for _ in range(q.qsize())]
    assert seqs[-1] == total - 1  # newest survived
    assert seqs == sorted(seqs)  # still in order
    assert min(seqs) >= 50  # the first 50 were dropped
