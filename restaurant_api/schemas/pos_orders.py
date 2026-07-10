"""Pydantic v2 schemas for POS session ordering + cash checkout (P1.3b / P1.4).

These sit on top of the table-session flow: a seated party's order is bound to
its ``table_session_id``. Lines are added one at a time from the POS (unlike
the bulk ingest ``OrderCreateRequest``), the unit price is snapshotted
server-side from the menu item, and checkout settles the bill in cash.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .orders import OrderResponse


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float literals are not accepted for money fields; use a string")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class LineAddRequest(BaseModel):
    """Add one item to the session's open order. Price is snapshotted from the
    menu item server-side — the POS never sends the price."""

    model_config = ConfigDict(frozen=True)

    menu_item_id: UUID
    qty: StrictDecimal = Field(default=Decimal("1"), gt=Decimal("0"))
    notes: str | None = Field(default=None, max_length=200)
    kitchen_station: Literal["kitchen", "bar", "dessert", "counter"] | None = None
    actor_id: UUID | None = None


class LineUpdateRequest(BaseModel):
    """Change a line's quantity (改量). line_total is recomputed server-side."""

    model_config = ConfigDict(frozen=True)

    qty: StrictDecimal = Field(gt=Decimal("0"))
    actor_id: UUID | None = None


class LineVoidRequest(BaseModel):
    """退菜 — remove a line and reverse its stock consumption. Audited."""

    model_config = ConfigDict(frozen=True)

    reason: str | None = Field(default=None, max_length=200)
    actor_id: UUID | None = None


class CheckoutCashRequest(BaseModel):
    """Settle the session's bill in cash. ``tendered`` must cover the total."""

    model_config = ConfigDict(frozen=True)

    tendered: StrictDecimal = Field(ge=Decimal("0"))
    actor_id: UUID | None = None


class CheckoutResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    order: OrderResponse
    total: Decimal
    tendered: Decimal
    change: Decimal


__all__ = [
    "CheckoutCashRequest",
    "CheckoutResult",
    "LineAddRequest",
    "LineUpdateRequest",
    "LineVoidRequest",
    "StrictDecimal",
]
