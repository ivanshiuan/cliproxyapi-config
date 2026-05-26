"""Customer (會員) master + points ledger.

Phase 1 carries the **minimum** customer surface so that orders can already
reference a buyer when the cashier scans a LINE QR / phone — even though
the marketing-side LINE bot / point redemption flows ship in Phase 2.

Why we ship the schema now, not later:
- Adding ``customer_id`` to ``orders`` after-the-fact requires a backfill;
  starting from day one means every transaction can be attributed.
- LINE is the dominant Taiwan loyalty channel; pre-allocating ``line_user_id``
  avoids a painful migration when the LINE bot integration lands.
"""

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
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import CITEXT
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


class CustomerTier(enum.StrEnum):
    """Loyalty tier. Recomputed nightly from spend; manual upgrade allowed."""

    REGULAR = "regular"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class Customer(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A known buyer — anyone we've identified beyond an anonymous walk-in.

    Identification can come from: LINE login, phone number, email, or just
    a verbal "I'm member #12345". ``line_user_id`` and ``phone`` are both
    unique-per-tenant when populated (partial unique indexes).
    """

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # CITEXT for case-insensitive uniqueness (LINE IDs are mixed case in the
    # wild, phone numbers occasionally have spaces).
    phone: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    line_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    tier: Mapped[CustomerTier] = mapped_column(
        SQLEnum(CustomerTier, name="customer_tier", native_enum=False, length=16),
        nullable=False,
        default=CustomerTier.REGULAR,
        server_default=CustomerTier.REGULAR.value,
    )
    # Materialised running totals — nightly job refreshes these. Source of
    # truth is the points_ledger / orders, but caching here avoids window
    # aggregations on every read.
    points_balance: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    lifetime_value: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    last_visit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Per-tenant uniqueness when populated. We don't enforce uniqueness
        # of (tenant_id, NULL) — multiple anonymous-ish accounts are fine.
        Index(
            "uq_customers_phone",
            "tenant_id",
            "phone",
            unique=True,
            postgresql_where="phone IS NOT NULL AND deleted_at IS NULL",
        ),
        Index(
            "uq_customers_line",
            "tenant_id",
            "line_user_id",
            unique=True,
            postgresql_where="line_user_id IS NOT NULL AND deleted_at IS NULL",
        ),
        Index(
            "uq_customers_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where="email IS NOT NULL AND deleted_at IS NULL",
        ),
        Index("ix_customers_tenant_tier", "tenant_id", "tier"),
        Index("ix_customers_tenant_last_visit", "tenant_id", "last_visit_at"),
    )


class CustomerPointsLedger(TenantScopedMixin, Base):
    """Append-only ledger of every point grant / redemption / expiry.

    Same model as ``stock_movements`` — never UPDATE, never DELETE; corrections
    are new reversing rows. Customer.points_balance is recomputed from this.
    """

    __tablename__ = "customer_points_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Signed: + for grants, - for redemptions/expiry.
    delta: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # Dotted reason namespace: "order.earn", "redeem.coupon",
    # "manual.adjust", "expire.batch", "birthday.gift", "referral.bonus".
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    source_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # NULL = points don't expire. Otherwise nightly expiry job zeros them.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_points_customer_time", "customer_id", "created_at"),
        Index("ix_points_reason", "reason"),
    )


__all__ = ["Customer", "CustomerPointsLedger", "CustomerTier"]
