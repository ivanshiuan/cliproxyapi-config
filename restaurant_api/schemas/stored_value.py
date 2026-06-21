"""Pydantic v2 schemas for the 儲值 (stored-value / prepaid wallet) endpoints.

Money fields use ``StrictDecimal`` (rejects ``float`` at the boundary) per the
project money rule. Responses project the append-only ledger / cached balance.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _reject_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("float not allowed; use Decimal or string")
    return v


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class StoredValueTopUpRequest(BaseModel):
    """Top up a member's prepaid wallet at the counter.

    ``amount`` is what the customer actually pays in; the service adds any
    promotional bonus on top (儲 1000 送 200) and writes both as separate
    ledger rows so the bonus liability is auditable.
    """

    model_config = ConfigDict(frozen=True)

    amount: StrictDecimal = Field(gt=0, le=1_000_000)
    actor_id: UUID | None = None
    note: str | None = Field(default=None, max_length=200)


class StoredValueSpendRequest(BaseModel):
    """Pay with stored value at the counter (writes a NEGATIVE ledger row)."""

    model_config = ConfigDict(frozen=True)

    amount: StrictDecimal = Field(gt=0, le=1_000_000)
    source_order_id: UUID | None = None
    actor_id: UUID | None = None
    note: str | None = Field(default=None, max_length=200)


class StoredValueLedgerResponse(BaseModel):
    """One row of the stored-value history. Append-only; read-only projection."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    delta: Decimal
    reason: str
    source_order_id: UUID | None
    note: str | None
    created_at: datetime


class StoredValueBalanceResponse(BaseModel):
    """Current cached wallet balance for a member."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: UUID
    stored_value_balance: Decimal


__all__ = [
    "StoredValueBalanceResponse",
    "StoredValueLedgerResponse",
    "StoredValueSpendRequest",
    "StoredValueTopUpRequest",
]
