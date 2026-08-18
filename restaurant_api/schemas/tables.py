"""Pydantic v2 schemas for the ``/tables`` router — 桌位管理.

Dining tables (桌號 / 區域 / 座位數) drive the dine-in front-end: QR code
per table, KDS table labels, and reservation seating. Names must be unique
per store among *live* rows (the DB enforces this via the partial unique
index ``uq_dining_tables_store_name_live``); soft-deleted tables free
their name for re-creation after a floor re-layout.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# 桌名 like "A1" / "吧台3" / "戶外2" — short by design (staff shorthand +
# printed QR labels). Whitespace-only names are rejected by the stripper.


def _strip_and_require(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
    return value


TableName = Annotated[
    str,
    BeforeValidator(_strip_and_require),
    Field(min_length=1, max_length=40),
]
ZoneName = Annotated[
    str,
    BeforeValidator(_strip_and_require),
    Field(min_length=1, max_length=40),
]
# 1..50 seats — a "table" seating more than 50 is a banquet layout problem,
# not a POS row.
Capacity = Annotated[int, Field(gt=0, le=50)]
SortOrder = Annotated[int, Field(ge=0, le=100_000)]


class TableCreateRequest(BaseModel):
    """Create one dining table in a store."""

    model_config = ConfigDict(frozen=True)

    store_id: UUID
    name: TableName
    zone: ZoneName | None = None
    capacity: Capacity = 4
    sort_order: SortOrder = 0


class TablePatchRequest(BaseModel):
    """Partial update — only fields present in the JSON body are applied."""

    model_config = ConfigDict(frozen=True)

    name: TableName | None = None
    zone: ZoneName | None = None
    capacity: Capacity | None = None
    sort_order: SortOrder | None = None
    is_active: bool | None = None


class TableResponse(BaseModel):
    """DiningTable row projected for HTTP."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    store_id: UUID
    name: str
    zone: str | None
    capacity: int
    sort_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "TableCreateRequest",
    "TablePatchRequest",
    "TableResponse",
]
