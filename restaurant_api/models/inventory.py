"""Inventory — ingredients master, BOM recipes, and the append-only ledger."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    Money,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class MovementType(enum.StrEnum):
    """Closed set of reasons a stock_movements row exists.

    Sign convention is enforced in the DB (see ``docs/04_data_schema.md``):
    inbound types are positive quantity, outbound types are negative.
    """

    PURCHASE = "purchase"
    SALE_CONSUME = "sale_consume"
    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"
    WASTE = "waste"
    STAFF_MEAL = "staff_meal"
    TASTING = "tasting"
    EXPIRY = "expiry"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class Ingredient(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """Raw material master record — what gets purchased and consumed."""

    __tablename__ = "ingredients"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    # Used for theoretical COGS only — actual COGS comes from the ledger's
    # weighted-average unit cost at the time of each consumption event.
    standard_cost_per_unit: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    reorder_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
    )
    # Food-safety: per-batch traceability. Populated on purchase; copied onto
    # every stock_movement that consumes from this lot. Lets us answer
    # "which orders used batch #PO-0512 of chicken?" during a 食安事件回溯.
    lot_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # FIFO + 即期警示: nightly job flags ingredients within N days of expiring.
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    __table_args__ = (
        Index(
            "ix_ingredients_tenant_store_active",
            "tenant_id",
            "store_id",
            "is_active",
        ),
    )


class Recipe(TenantScopedMixin, TimestampedMixin, Base):
    """BOM line: how much of an ingredient one serving of a menu item uses.

    Versioned with ``effective_from`` / ``effective_to`` so price-impacting
    recipe changes are auditable. The "current" version invariant — that
    only one row per (menu_item_id, ingredient_id) has ``effective_to IS
    NULL`` — is enforced by a partial unique index below.
    """

    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # CASCADE: if a menu item is hard-deleted (rare, only in dev) its recipe
    # rows go with it. Menu items normally soft-delete, in which case the
    # recipe is preserved.
    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    qty_per_serving: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        nullable=False,
    )
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        server_default=func.current_date(),
    )
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Partial unique: at most one "current" row per (menu_item, ingredient).
        Index(
            "uq_recipes_current",
            "menu_item_id",
            "ingredient_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )


class StockMovement(TenantScopedMixin, Base):
    """Append-only ledger of every inventory change.

    THIS TABLE IS IMMUTABLE.

    - No UPDATE, no DELETE. Enforced at the DB level by ``INSTEAD NOTHING``
      rules created in the migration layer (see docs/04 §3). We mirror the
      intent at the ORM level by NOT exposing an ``updated_at`` column and
      by NOT inheriting ``TimestampedMixin`` (only ``created_at``).
    - Corrections happen as reversal entries, never as edits.

    ``source_table`` + ``source_id`` give a generic pointer back to the
    business event that caused the movement (order line, waste event,
    staff meal, etc.). We deliberately don't use polymorphic FKs — the
    pair is denormalised on purpose so the ledger stays self-contained.
    """

    __tablename__ = "stock_movements"

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
    movement_type: Mapped[MovementType] = mapped_column(
        SQLEnum(MovementType, name="stock_movement_type", native_enum=False, length=32),
        nullable=False,
    )
    # Signed: positive = inbound, negative = outbound. The DB-level CHECK
    # constraint (from docs/04) enforces sign-by-type — we don't duplicate
    # it in the ORM but rely on the migration to install it.
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    source_table: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    # Food-safety: copied from the ingredient lot at the moment of consumption.
    # Lets a food-poisoning investigation trace from a customer order back to
    # the exact supplier batch — *without* having to join through ingredients
    # (which can be edited; the ledger row cannot).
    lot_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_stock_movements_ingredient_time",
            "ingredient_id",
            "occurred_at",
        ),
        Index(
            "ix_stock_movements_store_time",
            "store_id",
            "occurred_at",
        ),
        Index(
            "ix_stock_movements_type_time",
            "store_id",
            "movement_type",
            "occurred_at",
        ),
    )


__all__ = ["Ingredient", "MovementType", "Recipe", "StockMovement"]
