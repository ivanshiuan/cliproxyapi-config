"""Tenant — the company / legal entity (one row per 統編 in production)."""

from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampedMixin, uuid7


class Tenant(TimestampedMixin, Base):
    """One row per legal entity that uses the system.

    The first-store MVP launches with a single tenant; the schema is
    designed so chains / 加盟 can be onboarded later by inserting more rows
    rather than reshaping the data model.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)


__all__ = ["Tenant"]
