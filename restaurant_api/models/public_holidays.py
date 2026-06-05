"""Taiwan public-holiday calendar.

National holidays — NOT tenant-scoped. Used by the clock service's
``IsHolidayFn`` to decide whether a worked day routes into the
``holiday_hours`` bucket under 勞動基準法 §39 (holiday-rate pay).

Why a table rather than a hardcoded list:
- The list shifts every year (lunar holidays + government make-up days).
- Operators occasionally need to override (e.g. "we treat 母親節 as a
  holiday at this branch" — modelled later by a tenant-scoped override
  table; this one stays national-canonical).
- It gives us a stable join target for future read-side dashboards
  ("hours worked on national holidays this quarter").
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampedMixin, uuid7


class PublicHoliday(TimestampedMixin, Base):
    """One row per (country, date) for a recognised national public holiday."""

    __tablename__ = "public_holidays"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # ISO 3166-1 alpha-2; Taiwan is "TW". Kept generic so the same table can
    # serve overseas branches when the company expands (one branch in Tokyo
    # would query country="JP" rows).
    country: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        default="TW",
        server_default="TW",
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Free-form note — e.g. "make-up day per Executive Yuan announcement"
    # for the swap-around days that aren't intrinsic holidays.
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        # One row per country/date — duplicates would double-count holiday
        # hours and overpay employees.
        Index(
            "uq_public_holidays_country_date",
            "country",
            "holiday_date",
            unique=True,
        ),
    )


__all__ = ["PublicHoliday"]
