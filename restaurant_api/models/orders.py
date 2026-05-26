"""Orders — header + lines + discounts + payments (the sales side)."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
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


class OrderStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    VOIDED = "voided"
    REFUNDED = "refunded"


class DiscountKind(enum.StrEnum):
    """Closed set of discount kinds.

    - ``percent``: ``value`` is 0..1 (e.g. 0.10 = 10% off)
    - ``amount``: ``value`` is a fixed TWD amount
    - ``comp``: 招待 (the line is on the house)
    - ``allowance``: 折讓 (post-sale credit)
    - ``employee``: 員工優惠
    """

    PERCENT = "percent"
    AMOUNT = "amount"
    COMP = "comp"
    ALLOWANCE = "allowance"
    EMPLOYEE = "employee"


class PaymentMethod(enum.StrEnum):
    CASH = "cash"
    CREDIT = "credit"
    LINEPAY = "linepay"
    APPLEPAY = "applepay"
    JKO = "jko"
    UBEREATS = "ubereats"
    FOODPANDA = "foodpanda"
    VOUCHER = "voucher"
    OTHER = "other"


class Order(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """Sales order header — POS-agnostic; ingested from iCHEF / POS+ / manual.

    ``business_date`` is the operational day (separate from ``opened_at``)
    because late-night service crosses midnight and operators want the
    revenue booked against the day the shift began on. P&L joins on
    ``business_date``, not on the calendar date of ``opened_at``.
    """

    __tablename__ = "orders"

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
    order_no: Mapped[str] = mapped_column(Text, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        SQLEnum(OrderStatus, name="order_status", native_enum=False, length=16),
        nullable=False,
        default=OrderStatus.OPEN,
        server_default=OrderStatus.OPEN.value,
    )

    # 統一發票 fields — Taiwan-specific.
    invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    carrier_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    carrier_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_tax_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # POS integration round-trip.
    external_pos_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pos_source: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    discounts: Mapped[list[OrderDiscount]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    payments: Mapped[list[OrderPayment]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_orders_store_business_date", "store_id", "business_date"),
    )


class OrderLine(TenantScopedMixin, Base):
    """One sold item on an order — drives BOM auto-deduct via stock_movements.

    ``unit_price`` is a snapshot at sale time (menu prices change; receipts
    must not). ``line_total`` is stored rather than computed so the DB row
    can be audited offline without rerunning recipe joins.

    ``cogs_actual`` / ``cogs_theoretical`` are populated by the
    consumption-recording job; they're the per-line projection of the
    P&L view's COGS columns.
    """

    __tablename__ = "order_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # CASCADE here is intentional and safe: a deleted order has no lines.
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cogs_actual: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cogs_theoretical: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderDiscount(TenantScopedMixin, Base):
    """A discount / comp / allowance attached to an order (or line)."""

    __tablename__ = "order_discounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[DiscountKind] = mapped_column(
        SQLEnum(DiscountKind, name="order_discount_kind", native_enum=False, length=16),
        nullable=False,
    )
    # Either a percentage (0..1) or a TWD amount, depending on ``kind``.
    value: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SET NULL: don't lose the discount record if the approving employee is
    # later removed from the system.
    applied_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    order: Mapped[Order] = relationship(back_populates="discounts")


class OrderPayment(TenantScopedMixin, Base):
    """A tender against an order. ``fee_amount`` is critical for real P&L."""

    __tablename__ = "order_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    method: Mapped[PaymentMethod] = mapped_column(
        SQLEnum(PaymentMethod, name="order_payment_method", native_enum=False, length=16),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # Platform / card fees — feeds the platform_fees CTE in mv_daily_pnl.
    fee_amount: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    order: Mapped[Order] = relationship(back_populates="payments")

    __table_args__ = (
        Index("ix_order_payments_method_paid_at", "method", "paid_at"),
    )


__all__ = [
    "DiscountKind",
    "Order",
    "OrderDiscount",
    "OrderLine",
    "OrderPayment",
    "OrderStatus",
    "PaymentMethod",
]
