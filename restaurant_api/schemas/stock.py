"""Pydantic v2 schemas for the ``/stock`` router.

Conventions:
- Inputs are ``frozen=True, strict=True`` — clients must send Decimals as
  JSON strings (or integers); floats are rejected at parse time so monetary
  precision is never silently lost.
- All money / quantity values are ``Decimal`` (4 dp on the wire); we never
  funnel through ``float``.
- Responses use ``from_attributes=True`` so the service can ``model_validate``
  ORM rows directly.

Note: The spec lets us stash supplier/invoice metadata (and ``unit_cost``)
inside ``stock_movements.note`` as JSON until the dedicated ``purchase_orders``
ORM tables land. ``_NotePayload`` is the canonical encoding for that JSON
string — see ``services/stock_service.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _reject_float(value: Any) -> Any:
    """Pre-validator: refuse ``float`` inputs for money/qty fields.

    Pydantic v2 will happily coerce a JSON ``float`` into ``Decimal`` in lax
    mode — but a float that's been through ``json.loads`` has already lost
    precision (``0.1`` → ``0.1000000000000000055...``). We bounce them at
    422 so callers send strings (or integers) instead.
    """
    if isinstance(value, float):
        raise ValueError("must be a string or integer, not a float (precision unsafe)")
    return value


# Annotated alias: a Decimal field that accepts JSON strings/ints but rejects
# JSON floats. Used for qty / unit_cost / delta below.
SafeDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]

# ──────────────────────────────────────────────────────────────────────────
# Requests
# ──────────────────────────────────────────────────────────────────────────


class PurchaseLineCreate(BaseModel):
    """One line on a supplier invoice — one ingredient, one lot, one price."""

    # ``strict=True`` would also reject JSON-string UUIDs and JSON-string
    # Decimals, which is *too* strict for an HTTP API. The ``SafeDecimal``
    # alias re-introduces the only strictness that matters financially:
    # JSON ``float`` is forbidden for qty/unit_cost.
    model_config = ConfigDict(frozen=True)

    ingredient_id: UUID
    qty: SafeDecimal = Field(gt=Decimal("0"))
    unit_cost: SafeDecimal = Field(ge=Decimal("0"))
    # Forward-compat: stored on the ledger row's ``lot_no`` column today; once
    # the PO ORM lands this stays exactly the same on the wire.
    lot_no: str | None = Field(default=None, max_length=64)
    expires_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


class PurchaseCreateRequest(BaseModel):
    """A single supplier invoice → N ledger rows of type ``purchase``."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    supplier_name: str = Field(min_length=1, max_length=100)
    supplier_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    invoice_number: str = Field(min_length=1, max_length=20)
    invoice_date: date
    received_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=1000)
    lines: list[PurchaseLineCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def _invoice_date_not_future(self) -> PurchaseCreateRequest:
        # Compared in UTC date terms — close enough for an MVP and saner than
        # forcing every test to pin a tz.
        from datetime import UTC
        from datetime import datetime as _dt

        today = _dt.now(UTC).date()
        if self.invoice_date > today:
            raise ValueError("invoice_date must not be in the future")
        if self.received_at is not None and self.received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        return self


class AdjustmentCreateRequest(BaseModel):
    """A single 盤點 line — supplies the signed delta and a reason."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    ingredient_id: UUID
    delta: SafeDecimal  # non-zero enforced below; float rejected at parse time
    reason: str = Field(min_length=1, max_length=200)
    occurred_at: datetime | None = None
    external_ref: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _delta_non_zero(self) -> AdjustmentCreateRequest:
        if self.delta == Decimal("0"):
            raise ValueError("delta must be non-zero")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


# ──────────────────────────────────────────────────────────────────────────
# Responses
# ──────────────────────────────────────────────────────────────────────────


MovementTypeLiteral = Literal[
    "purchase",
    "sale_consume",
    "adjustment_in",
    "adjustment_out",
    "waste",
    "staff_meal",
    "tasting",
    "expiry",
    "transfer_in",
    "transfer_out",
]


class StockMovementResponse(BaseModel):
    """Generic ledger-row read shape — used by GET /stock/movements."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    ingredient_id: UUID
    movement_type: MovementTypeLiteral
    qty: Decimal
    source_table: str | None
    source_id: UUID | None
    lot_no: str | None
    occurred_at: datetime
    note: str | None


class PurchaseLineResponse(BaseModel):
    """One synthesised line — reverse-derived from a ``purchase`` ledger row."""

    model_config = ConfigDict(from_attributes=True)

    ingredient_id: UUID
    qty: Decimal
    unit_cost: Decimal
    line_total: Decimal
    lot_no: str | None
    expires_on: date | None
    movement_id: UUID


class PurchaseResponse(BaseModel):
    """A purchase invoice projection — one synthetic invoice id, N lines."""

    model_config = ConfigDict(from_attributes=True)

    purchase_invoice_id: UUID
    store_id: UUID
    supplier_name: str
    supplier_tax_id: str | None
    invoice_number: str
    invoice_date: date
    received_at: datetime
    total: Decimal
    notes: str | None
    lines: list[PurchaseLineResponse]


class AdjustmentResponse(BaseModel):
    """The single ``adjustment_in`` / ``adjustment_out`` row a 盤點 produced."""

    model_config = ConfigDict(from_attributes=True)

    movement_id: UUID
    store_id: UUID
    ingredient_id: UUID
    delta: Decimal
    reason: str
    occurred_at: datetime


__all__ = [
    "AdjustmentCreateRequest",
    "AdjustmentResponse",
    "MovementTypeLiteral",
    "PurchaseCreateRequest",
    "PurchaseLineCreate",
    "PurchaseLineResponse",
    "PurchaseResponse",
    "StockMovementResponse",
]
