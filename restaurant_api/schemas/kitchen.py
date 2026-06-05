"""Pydantic v2 schemas for the ``/kitchen`` router (KDS = Kitchen Display System).

Phase 1 KDS is order-line-centric: one ``OrderLine`` per dish, each routed
to a ``KitchenStation`` (kitchen / bar / dessert / counter) and progressed
through ``KitchenStatus`` (queued → cooking → ready → served).

The transition graph lives in ``KITCHEN_TRANSITIONS`` below — same pattern
as the reservation router.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import KitchenStation, KitchenStatus

# ──────────────────────────────────────────────────────────────────────────
# Transition graph — single source of truth.
# ──────────────────────────────────────────────────────────────────────────


KITCHEN_TRANSITIONS: dict[KitchenStatus, set[KitchenStatus]] = {
    KitchenStatus.QUEUED: {KitchenStatus.COOKING, KitchenStatus.CANCELLED},
    KitchenStatus.COOKING: {KitchenStatus.READY, KitchenStatus.CANCELLED},
    KitchenStatus.READY: {KitchenStatus.SERVED, KitchenStatus.CANCELLED},
    # Terminal states.
    KitchenStatus.SERVED: set(),
    KitchenStatus.CANCELLED: set(),
}


# ──────────────────────────────────────────────────────────────────────────
# Requests
# ──────────────────────────────────────────────────────────────────────────


class KitchenStatusPatch(BaseModel):
    """Transition an order line's KDS state. Router validates against
    ``KITCHEN_TRANSITIONS`` — invalid hop → 409."""

    model_config = ConfigDict(frozen=True)

    status: KitchenStatus
    actor_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


# ──────────────────────────────────────────────────────────────────────────
# Responses
# ──────────────────────────────────────────────────────────────────────────


class KitchenLineResponse(BaseModel):
    """One KDS-visible line. Joins ``order_lines`` + ``orders`` so the
    kitchen sees ``order_no`` (the visible ticket number) and
    ``business_date`` without a second fetch."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    order_id: UUID
    order_no: str
    store_id: UUID
    business_date: str  # ISO date — kitchen UIs treat it as opaque
    menu_item_id: UUID
    qty: float
    notes: str | None
    kitchen_station: KitchenStation
    kitchen_status: KitchenStatus
    sent_to_kitchen_at: datetime | None
    cooking_started_at: datetime | None
    ready_at: datetime | None
    served_at: datetime | None


__all__ = [
    "KITCHEN_TRANSITIONS",
    "KitchenLineResponse",
    "KitchenStatusPatch",
]
