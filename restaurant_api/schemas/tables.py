"""Pydantic v2 schemas for the ``/tables`` router (POS floor plan, P1.3).

Two objects: the physical ``DiningTable`` (CRUD) and the ``TableSession``
(one seating — open → closed/cancelled). The floor-plan board response joins
a table to its live open session so the POS can render 空桌 vs 用餐中 at a
glance.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import TableSessionStatus

# ──────────────────────────────────────────────────────────────────────────
# Dining tables (CRUD)
# ──────────────────────────────────────────────────────────────────────────


class DiningTableCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    store_id: UUID
    name: str = Field(min_length=1, max_length=40)
    zone: str | None = Field(default=None, max_length=40)
    seats: int = Field(default=4, gt=0, le=100)
    sort_order: int = Field(default=0, ge=0)


class DiningTableUpdate(BaseModel):
    """Partial update — only provided fields change."""

    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=40)
    zone: str | None = Field(default=None, max_length=40)
    seats: int | None = Field(default=None, gt=0, le=100)
    sort_order: int | None = Field(default=None, ge=0)


class DiningTableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID
    name: str
    zone: str | None
    seats: int
    sort_order: int
    qr_token: str | None
    created_at: datetime
    updated_at: datetime


# ──────────────────────────────────────────────────────────────────────────
# Table sessions (open / close / transfer)
# ──────────────────────────────────────────────────────────────────────────


class TableSessionOpen(BaseModel):
    """Seat a party at a table. Fails 409 if the table already has an open
    session (enforced at the DB level too)."""

    model_config = ConfigDict(frozen=True)

    party_size: int = Field(default=1, gt=0, le=200)
    opened_by: UUID | None = None
    reservation_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=500)


class TableSessionTransfer(BaseModel):
    """Move an open session to a different (free) table — 轉桌."""

    model_config = ConfigDict(frozen=True)

    target_table_id: UUID
    actor_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class TableSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID
    table_id: UUID
    status: TableSessionStatus
    party_size: int
    opened_at: datetime
    closed_at: datetime | None
    opened_by: UUID | None
    reservation_id: UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class FloorTableView(BaseModel):
    """One table on the floor-plan board + its live session (if any)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    zone: str | None
    seats: int
    sort_order: int
    is_occupied: bool
    session: TableSessionResponse | None


__all__ = [
    "DiningTableCreate",
    "DiningTableResponse",
    "DiningTableUpdate",
    "FloorTableView",
    "TableSessionOpen",
    "TableSessionResponse",
    "TableSessionTransfer",
]
