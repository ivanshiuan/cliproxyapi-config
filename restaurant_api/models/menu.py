"""Menu — categories + sellable items (POS-agnostic master)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    Money,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class MenuCategory(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A grouping of menu items (前菜 / 主餐 / 飲料).

    ``store_id`` is nullable so a category can be shared across stores in a
    chain rollout; for a single-store tenant it'll typically be set.
    """

    __tablename__ = "menu_categories"

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

    items: Mapped[list[MenuItem]] = relationship(
        back_populates="category",
        passive_deletes=True,
    )


class MenuItem(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A sellable menu item — what shows up on a POS / kiosk / menu board.

    The ``external_pos_id`` / ``pos_source`` pair is used by the
    iCHEF / POS+ integration layer to round-trip items with the external
    POS without losing identity.
    """

    __tablename__ = "menu_items"

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
    # SET NULL: if a category is deleted we keep the item — it just falls
    # into the "uncategorised" bucket until reassigned.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sku: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # Allergen codes per Taiwan 消保法 (11 大過敏原): e.g.
    # ["milk","egg","wheat","peanut","tree_nut","shellfish","fish","soy",
    #  "sulfite","sesame","mango"]. Empty list = no declared allergens.
    allergens: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    is_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    external_pos_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pos_source: Mapped[str | None] = mapped_column(Text, nullable=True)

    category: Mapped[MenuCategory | None] = relationship(back_populates="items")

    __table_args__ = (
        # Tenant-scoped SKU uniqueness for live (non-deleted) rows. Partial
        # so soft-deleted rows can have their SKUs reused.
        Index(
            "uq_menu_items_tenant_sku_live",
            "tenant_id",
            "sku",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_menu_items_tenant_store_available",
            "tenant_id",
            "store_id",
            "is_available",
        ),
    )


__all__ = ["MenuCategory", "MenuItem"]
