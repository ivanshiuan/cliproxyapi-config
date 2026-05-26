"""Cost events — the typed-row record of every hidden cost leak.

These three tables (waste / staff meal / tasting) each produce one
``stock_movements`` row and one cost line in ``mv_daily_pnl``. They're how
the product actually delivers on its core promise: every leak is auditable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Money, TenantScopedMixin, uuid7


class WasteEvent(TenantScopedMixin, Base):
    """報廢: an ingredient went bad / got dropped / was over-prepped."""

    __tablename__ = "waste_events"

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
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # SET NULL: don't void the waste record if the reporting employee leaves.
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_waste_events_store_occurred", "store_id", "occurred_at"),
    )


class StaffMealEvent(TenantScopedMixin, Base):
    """員工餐: a meal consumed by staff.

    Two shapes are supported:
    - Prepared meal: ``menu_item_id`` set, ``ingredients_used`` NULL — cost
      is computed from the recipe.
    - Ad-hoc: ``menu_item_id`` NULL, ``ingredients_used`` is a JSON map of
      ``{ingredient_id: qty}`` — used when staff cooks something not on
      the menu.
    """

    __tablename__ = "staff_meal_events"

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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    ingredients_used: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_staff_meal_events_store_occurred", "store_id", "occurred_at"),
    )


class TastingEvent(TenantScopedMixin, Base):
    """試吃 / 試菜: R&D tastings, VIP samples, training portions, QC checks."""

    __tablename__ = "tasting_events"

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
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_tasting_events_store_occurred", "store_id", "occurred_at"),
    )


__all__ = ["StaffMealEvent", "TastingEvent", "WasteEvent"]
