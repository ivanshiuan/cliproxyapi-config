"""Menu — categories, sellable items, and modifier groups (POS-agnostic master)."""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, String, Text, text
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
    modifier_links: Mapped[list[MenuItemModifierGroup]] = relationship(
        back_populates="menu_item",
        passive_deletes=True,
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


class ModifierSelectionType(enum.StrEnum):
    """How many options a customer picks from a modifier group."""

    SINGLE = "single"  # radio — exactly one (甜度、冰量、熟度)
    MULTI = "multi"  # checkboxes — zero or more, bounded by min/max (加料)


class ModifierGroup(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A reusable customisation group (甜度 / 冰量 / 加料 / 醬料).

    Groups are defined once and linked to any number of menu items through
    ``menu_item_modifier_groups`` so a "甜度" group is maintained in one
    place. ``min_select`` / ``max_select`` bound MULTI groups; a SINGLE
    group with ``is_required=True`` is the classic mandatory radio.
    """

    __tablename__ = "modifier_groups"

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
    selection_type: Mapped[ModifierSelectionType] = mapped_column(
        # values_callable: persist the StrEnum *values* ("single"/"multi") —
        # the migrated PG enum labels are lowercase; SQLAlchemy's default of
        # persisting member *names* ("SINGLE") would fail on every bind/fetch.
        Enum(
            ModifierSelectionType,
            name="modifier_selection_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ModifierSelectionType.SINGLE,
        server_default="single",
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    min_select: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    # NULL = unbounded (multi). For SINGLE groups the service layer treats
    # max_select as 1 regardless of what's stored.
    max_select: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    options: Mapped[list[ModifierOption]] = relationship(
        back_populates="group",
        passive_deletes=True,
        order_by="ModifierOption.sort_order",
    )
    item_links: Mapped[list[MenuItemModifierGroup]] = relationship(
        back_populates="group",
        passive_deletes=True,
    )


class ModifierOption(TenantScopedMixin, TimestampedMixin, Base):
    """One choice inside a modifier group (微糖 / 珍珠 +10 / 五分熟)."""

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
    price_delta: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    is_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    group: Mapped[ModifierGroup] = relationship(back_populates="options")


class MenuItemModifierGroup(TenantScopedMixin, Base):
    """Link table attaching a modifier group to a menu item (batch reuse)."""

    __tablename__ = "menu_item_modifier_groups"

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
    modifier_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("modifier_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    menu_item: Mapped[MenuItem] = relationship(back_populates="modifier_links")
    group: Mapped[ModifierGroup] = relationship(back_populates="item_links")

    __table_args__ = (
        Index(
            "uq_menu_item_modifier_groups_pair",
            "menu_item_id",
            "modifier_group_id",
            unique=True,
        ),
    )


__all__ = [
    "MenuCategory",
    "MenuItem",
    "MenuItemModifierGroup",
    "ModifierGroup",
    "ModifierOption",
    "ModifierSelectionType",
]
