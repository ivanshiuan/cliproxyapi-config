"""Dining tables — 桌位管理 for dine-in ordering (桌號 / 區域 / 前台顯示)."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class DiningTable(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A physical table in a store (A1 / 吧台3 / 戶外2).

    ``is_active`` controls whether the table shows on the ordering front-end
    (桌位可建好但先不顯示); soft delete keeps historical orders' table
    references resolvable after a re-layout.
    """

    __tablename__ = "dining_tables"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    zone: Mapped[str | None] = mapped_column(String, nullable=True)
    capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=4,
        server_default="4",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    __table_args__ = (
        # Table names must be unique per store among live rows so QR codes
        # and staff shorthand stay unambiguous; soft-deleted rows free the name.
        Index(
            "uq_dining_tables_store_name_live",
            "store_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


__all__ = ["DiningTable"]
