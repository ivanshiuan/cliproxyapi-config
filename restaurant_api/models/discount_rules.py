"""自動折扣規則 (docs/22 #10) — 滿額/時段自動套用的折扣。

A rule is a *condition* (滿額門檻 and/or 每日時段窗口) plus a *discount*
(percent 0..1 or a fixed TWD amount). Matching + application live in
``services/discount_rule_service`` / ``services/pos_order_service``; the
applied result is an ordinary ``OrderDiscount`` row (tagged ``rule_id``)
so the existing ``amount_due`` discount stack does all the money math —
no second pricing engine.
"""

from __future__ import annotations

import uuid
from datetime import time
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, String, Time
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Money, SoftDeleteMixin, TenantScopedMixin, TimestampedMixin, uuid7
from .orders import DiscountKind


class DiscountRule(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """One auto-discount rule. ``store_id`` NULL = every store in the tenant."""

    __tablename__ = "discount_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Only PERCENT / AMOUNT are valid here (service-enforced): comp/allowance/
    # employee are inherently manual, per-situation decisions.
    kind: Mapped[DiscountKind] = mapped_column(
        SQLEnum(DiscountKind, name="order_discount_kind", native_enum=False, length=16),
        nullable=False,
    )
    value: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # 滿額門檻: applies when the order's gross (pre-discount line total) is
    # >= this. NULL = no amount condition.
    min_subtotal: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # 時段窗口 (Asia/Taipei wall clock). Both NULL = all day; start > end
    # wraps past midnight — same semantics as MenuItem's availability window.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
