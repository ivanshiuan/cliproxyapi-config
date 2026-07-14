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


class OrderType(enum.StrEnum):
    """How the meal is consumed. Drives table binding + packaging flow."""

    DINE_IN = "dine_in"
    TAKEOUT = "takeout"
    DELIVERY = "delivery"


class OrderChannel(enum.StrEnum):
    """Where the order was captured.

    ``external`` covers legacy ingest from third-party POS (the original
    Phase-1 flow via ``external_pos_id``); the in-house POS surfaces use the
    other values.
    """

    POS = "pos"  # staff POS terminal
    TABLE_QR = "table_qr"  # customer phone via 桌位/訂單 QR
    TABLET = "tablet"  # table-side kiosk tablet
    ONLINE = "online"  # web takeout / delivery storefront
    EXTERNAL = "external"  # ingested from an external POS


class InvoiceStatus(enum.StrEnum):
    """Lifecycle of a Taiwan 統一發票 attached to this order.

    ``pending``  — order finalised but no invoice issued yet (typical for
                   walk-in cash sales that get a paper invoice at the end of
                   the day).
    ``issued``   — invoice number assigned & uploaded to 財政部 (cloud) or
                   printed (paper).
    ``voided``   — 作廢 within the same period.
    ``allowance``— 折讓 (C0701) certificate issued for full/partial refund.
    ``winner``   — drew a prize in the 統一發票對獎 lottery.
    ``redeemed`` — prize claimed by the buyer (we tracked it back to them
                   for marketing follow-up).
    """

    PENDING = "pending"
    ISSUED = "issued"
    VOIDED = "voided"
    ALLOWANCE = "allowance"
    WINNER = "winner"
    REDEEMED = "redeemed"


class InvoiceMedia(enum.StrEnum):
    """雲端 vs 紙本 — affects when the invoice must be uploaded to MoF."""

    CLOUD = "cloud"  # 雲端發票 — uploaded via API on issue
    PAPER = "paper"  # 紙本 / 收銀機發票 — uploaded in monthly batch


class KitchenStation(enum.StrEnum):
    """Where this order line gets prepared. Drives KDS routing."""

    KITCHEN = "kitchen"  # 主廚房 — hot dishes
    BAR = "bar"  # 飲料吧 — drinks, cocktails
    DESSERT = "dessert"  # 甜點 / cold prep
    COUNTER = "counter"  # 外帶 / 收銀 prep (no kitchen required)


class KitchenStatus(enum.StrEnum):
    """KDS lifecycle of a single order line.

    Transitions:
        queued → cooking → ready → served
        ↓
        cancelled (any time before ``served``)
    """

    QUEUED = "queued"  # entered KDS, awaiting cook
    COOKING = "cooking"  # cook acknowledged + started
    READY = "ready"  # plated, waiting for runner
    SERVED = "served"  # delivered to table / counter
    CANCELLED = "cancelled"  # voided before service


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
    # SET NULL on customer delete: keep the sales record even if the
    # customer profile is removed (right-to-erasure / 個資法 §11). The
    # historical revenue stays attributed to the store; only the
    # personal-data link drops.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
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

    # ─── POS front-of-house (P1) ───
    # Pre-existing ingested rows default to dine_in/external; the in-house
    # POS sets these explicitly on creation.
    # NB: SQLEnum(native_enum=False) stores the member NAME ("DINE_IN"), not
    # the value — the server_default backfills legacy rows, so it must be the
    # NAME or those rows become unreadable through the ORM.
    order_type: Mapped[OrderType] = mapped_column(
        SQLEnum(OrderType, name="order_type", native_enum=False, length=16),
        nullable=False,
        default=OrderType.DINE_IN,
        server_default=OrderType.DINE_IN.name,
    )
    channel: Mapped[OrderChannel] = mapped_column(
        SQLEnum(OrderChannel, name="order_channel", native_enum=False, length=16),
        nullable=False,
        default=OrderChannel.EXTERNAL,
        server_default=OrderChannel.EXTERNAL.name,
    )
    # 服務費 rate as a fraction (0.10 = 10%), applied on the line subtotal
    # (gross) and added AFTER the discount stack: due = discounted + gross*rate.
    # Persisted per order so quote / checkout / reports all read one number.
    service_charge_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    # SET NULL: an order must survive its seating record (financial ledger
    # outlives floor-plan history).
    table_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("table_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 統一發票 fields — Taiwan-specific.
    invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_status: Mapped[InvoiceStatus | None] = mapped_column(
        SQLEnum(InvoiceStatus, name="invoice_status", native_enum=False, length=16),
        nullable=True,
    )
    invoice_media: Mapped[InvoiceMedia | None] = mapped_column(
        SQLEnum(InvoiceMedia, name="invoice_media", native_enum=False, length=8),
        nullable=True,
    )
    # If invoice gets voided/replaced, the OLD number lives here so we can
    # cross-reference with MoF's records when reconciling.
    void_invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 折讓單 C0701 number if a partial refund is processed against this order.
    allowance_invoice_number: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # 套餐組合: set when this line is an auto-expanded component of a combo
    # item's line (see pos_order_service._expand_combo). NULL for a normal
    # line or a combo's own "header" line. CASCADE so voiding the parent
    # combo line takes its zero-price component tickets with it.
    combo_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cogs_actual: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cogs_theoretical: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # ─── KDS (kitchen display system) routing & status ───
    # Nullable to support legacy / counter-only orders that never reached
    # the kitchen. When set, the KDS surface picks the line up.
    kitchen_station: Mapped[KitchenStation | None] = mapped_column(
        SQLEnum(KitchenStation, name="kitchen_station", native_enum=False, length=16),
        nullable=True,
    )
    kitchen_status: Mapped[KitchenStatus | None] = mapped_column(
        SQLEnum(KitchenStatus, name="kitchen_status", native_enum=False, length=16),
        nullable=True,
    )
    # Timestamps for kitchen latency analytics. NULL when the corresponding
    # transition hasn't happened (or wasn't routed through KDS at all).
    sent_to_kitchen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cooking_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    served_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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
    modifiers: Mapped[list[OrderLineModifier]] = relationship(
        back_populates="order_line",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # KDS queue scans: "what's queued at the kitchen station, oldest first?"
        Index(
            "ix_order_lines_kitchen_queue",
            "kitchen_station",
            "kitchen_status",
            "sent_to_kitchen_at",
        ),
    )


class OrderLineModifier(TenantScopedMixin, TimestampedMixin, Base):
    """A selected 口味選項/加價購 choice on one order line.

    ``option_name`` / ``price_delta`` are snapshotted at selection time (same
    receipt-immutability reasoning as ``OrderLine.unit_price`` — the modifier
    catalog can change price later without rewriting past sales).
    """

    __tablename__ = "order_line_modifiers"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    modifier_option_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("modifier_options.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    option_name: Mapped[str] = mapped_column(Text, nullable=False)
    price_delta: Mapped[Decimal] = mapped_column(Money, nullable=False)

    order_line: Mapped[OrderLine] = relationship(back_populates="modifiers")


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
    "OrderChannel",
    "OrderDiscount",
    "OrderLine",
    "OrderPayment",
    "OrderStatus",
    "OrderType",
    "PaymentMethod",
]
