"""Menu — categories + sellable items (POS-agnostic master)."""

from __future__ import annotations

import uuid
from datetime import time
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, Time, text
from sqlalchemy import Enum as SQLEnum
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
from .orders import KitchenStation


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
    # 廚房自動分站: which KDS lane a line for this item lands on by default.
    # NULL falls back to "kitchen" (see pos_order_service._resolve_kitchen_station).
    # An explicit station on the add-line request still wins over this.
    default_kitchen_station: Mapped[KitchenStation | None] = mapped_column(
        SQLEnum(KitchenStation, name="kitchen_station", native_enum=False, length=16),
        nullable=True,
    )
    # 時段菜單: local (Asia/Taipei) wall-clock window this item is orderable in.
    # Both NULL = always available. available_start_time > available_end_time
    # means the window wraps past midnight (e.g. 宵夜 21:00-02:00).
    available_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    available_end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    category: Mapped[MenuCategory | None] = relationship(back_populates="items")
    modifier_groups: Mapped[list[ModifierGroup]] = relationship(
        back_populates="menu_item",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ModifierGroup.sort_order",
    )

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


class ModifierGroup(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """口味選項組 — a set of choices attached to one menu item.

    e.g. 甜度 (min_select=max_select=1, required single choice) or
    加料 (min_select=0, max_select=3, optional multi-pick add-ons).
    """

    __tablename__ = "modifier_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    min_select: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_select: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    menu_item: Mapped[MenuItem] = relationship(back_populates="modifier_groups")
    options: Mapped[list[ModifierOption]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ModifierOption.sort_order",
    )


class ModifierOption(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """One selectable choice within a :class:`ModifierGroup`.

    ``price_delta`` is added to the line's unit price per unit ordered when
    selected (0 for free choices like 正常糖, positive for paid add-ons like
    加蛋+10).
    """

    __tablename__ = "modifier_options"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("modifier_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_delta: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"), server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    group: Mapped[ModifierGroup] = relationship(back_populates="options")


class ComboComponent(TenantScopedMixin, TimestampedMixin, Base):
    """套餐組合 — fixed composition: ``combo_item`` bundles ``qty`` of
    ``component_item`` at no extra charge (already priced into the combo).

    When a combo item is added to an order, the order service auto-expands
    these into zero-price child order lines (see
    ``pos_order_service._expand_combo``) so the kitchen still gets a ticket
    per component, routed to that component's own station.
    """

    __tablename__ = "combo_components"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    combo_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    component_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("1"), server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


__all__ = ["ComboComponent", "MenuCategory", "MenuItem", "ModifierGroup", "ModifierOption"]
