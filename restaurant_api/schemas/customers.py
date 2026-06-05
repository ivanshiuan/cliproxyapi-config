"""Pydantic v2 schemas for ``/customers`` router.

CRUD shape:
  - ``CustomerCreate`` — POST body when onboarding a new member at the
    counter (cashier reads a LINE QR / phone number / member card).
  - ``CustomerUpdate`` — PATCH body. All fields optional; the service
    only applies what's present.
  - ``CustomerResponse`` — full row projected back to HTTP.
  - ``CustomerPointsLedgerResponse`` — append-only ledger row for the
    points-history endpoint.

Why no DELETE schema: the router exposes DELETE but the service writes
``deleted_at`` (soft delete) — the response is just the timestamped row,
so we re-use ``CustomerResponse``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..models import CustomerTier


def _reject_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("float not allowed; use Decimal or string")
    return v


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


# ──────────────────────────────────────────────────────────────────────────
# Requests
# ──────────────────────────────────────────────────────────────────────────


class CustomerCreate(BaseModel):
    """Onboard a new member.

    The cashier scans a LINE QR (or the customer dictates their phone) —
    those become the unique-per-tenant handles. ``display_name`` is the
    only truly required field for the receipt line.
    """

    model_config = ConfigDict(frozen=True)

    display_name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=120)
    line_user_id: str | None = Field(default=None, max_length=64)
    birthdate: date | None = None
    tier: CustomerTier = CustomerTier.REGULAR
    notes: str | None = None


class CustomerUpdate(BaseModel):
    """Partial update. Only fields present in the request are applied.

    Note: ``points_balance`` / ``lifetime_value`` / ``last_visit_at`` are
    cached aggregates maintained by the close-order path and the nightly
    job — operators cannot patch them directly (data-integrity boundary).
    Tier IS editable here (manual upgrade is a common F&B perk).
    """

    model_config = ConfigDict(frozen=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=120)
    line_user_id: str | None = Field(default=None, max_length=64)
    birthdate: date | None = None
    tier: CustomerTier | None = None
    notes: str | None = None


# ──────────────────────────────────────────────────────────────────────────
# Responses
# ──────────────────────────────────────────────────────────────────────────


class CustomerResponse(BaseModel):
    """Customer row projected for HTTP. Deleted rows still return when
    fetched by id (soft-delete is reversible)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    display_name: str
    phone: str | None
    email: str | None
    line_user_id: str | None
    birthdate: date | None
    tier: CustomerTier
    points_balance: Decimal
    lifetime_value: Decimal
    last_visit_at: datetime | None
    notes: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CustomerPointsLedgerResponse(BaseModel):
    """One row of the customer's points history. Append-only at the
    DB level; this is a read-only projection."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    delta: Decimal
    reason: str
    source_order_id: UUID | None
    expires_at: datetime | None
    note: str | None
    created_at: datetime


__all__ = [
    "CustomerCreate",
    "CustomerPointsLedgerResponse",
    "CustomerResponse",
    "CustomerUpdate",
]
