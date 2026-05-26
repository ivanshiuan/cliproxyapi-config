"""Store — a physical 門市 (designed for chains, MVP launches with one)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SoftDeleteMixin, TenantScopedMixin, TimestampedMixin, uuid7


class Store(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A physical store / 門市.

    Google Maps fields (``google_place_id``, ``latitude``, ``longitude``) are
    populated as part of forward-compat for the location-intelligence work
    that lands after MVP — they are not required at insert time.
    """

    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)

    # Google Maps forward-compat — populated by a later integration.
    google_place_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)

    opened_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    timezone: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="Asia/Taipei",
        server_default="Asia/Taipei",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    __table_args__ = (
        Index("ix_stores_tenant_active", "tenant_id", "is_active"),
    )


__all__ = ["Store"]
