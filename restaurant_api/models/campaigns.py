"""Marketing campaigns — the 開幕引流輪盤抽獎 (grand-opening wheel-spin) engine.

The drive-traffic loop this models:

1. A QR code at the storefront points the prospect at a campaign URL.
2. They spin the wheel (``campaign_spins``) — once per day per member.
3. Spinning requires (or creates) a ``Customer`` — that's the membership hook.
4. A win drops a time-bound voucher into the member's wallet
   (``campaign_vouchers``). The grand prize (免單四人 / 雙人套餐) is rate-limited
   by a per-prize quota so it stays scarce.
5. The member comes in and the counter redeems ONE voucher per visit — they
   pick the best one still in date. Lesser vouchers wait for their own expiry.

Design notes
------------
- ``campaign_spins`` is an **append-only log** (like the points ledger): one row
  per spin, never mutated. It's the audit trail behind every voucher and the
  source of truth for the "one spin per day" rule.
- ``campaign_vouchers`` is the mutable wallet record — a voucher transitions
  ``active → redeemed`` (or ``→ expired`` by the nightly sweep). Redemption is
  idempotency-guarded at the service layer.
- Voucher validity is a **fixed offset + fixed duration** (per the campaign
  config): ``valid_from = spin_date + start_offset_days`` and
  ``valid_until = valid_from + validity_days``. Stored as dates because the
  customer thinks in "本週六能用 / 效期兩週", not timestamps.
"""

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
    Integer,
    String,
    Text,
    func,
    text,
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


class CampaignStatus(enum.StrEnum):
    """Lifecycle of a campaign.

    ``draft``   — being configured; not spinnable.
    ``active``  — live; QR scans can spin (also gated by starts_at/ends_at).
    ``paused``  — temporarily halted (e.g. grand prize ran out, retuning odds).
    ``ended``   — finished; vouchers already issued stay redeemable until their
                  own ``valid_until``.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class VoucherStatus(enum.StrEnum):
    """Lifecycle of a single won voucher in the member's wallet.

    ``active``   — won, in date (or pending its ``valid_from``), redeemable.
    ``redeemed`` — claimed at the counter; terminal.
    ``expired``  — passed ``valid_until`` unredeemed; terminal (set by sweep job).
    ``void``     — cancelled by staff (fraud / mis-issue); terminal.
    """

    ACTIVE = "active"
    REDEEMED = "redeemed"
    EXPIRED = "expired"
    VOID = "void"


class MarketingCampaign(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """A wheel-spin lottery campaign — the top-level container.

    ``slug`` is the public handle baked into the QR URL (unique per live tenant).
    The voucher-validity knobs (``voucher_start_offset_days`` /
    ``voucher_validity_days``) implement the "固定起算 + 固定天數" rule.
    """

    __tablename__ = "marketing_campaigns"

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
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        SQLEnum(CampaignStatus, name="campaign_status", native_enum=False, length=16),
        nullable=False,
        default=CampaignStatus.DRAFT,
        server_default=CampaignStatus.DRAFT.value,
    )

    # Active window. Spins outside [starts_at, ends_at] are rejected even if
    # status == active. Both nullable = "no bound on that side".
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # How many spins one member gets per (Asia/Taipei) day. Default 1.
    daily_spin_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # Voucher validity rule: valid_from = spin_date + start_offset_days,
    # valid_until = valid_from + validity_days. Defaults: redeemable from the
    # day of the spin, good for two weeks.
    voucher_start_offset_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    voucher_validity_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=14, server_default="14"
    )

    # The 彈跳訊息 pushed to the member each day they log in to spin. Optional.
    daily_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    prizes: Mapped[list[CampaignPrize]] = relationship(
        back_populates="campaign",
        passive_deletes=True,
    )

    __table_args__ = (
        Index(
            "uq_campaigns_tenant_slug_live",
            "tenant_id",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
    )


class CampaignPrize(TenantScopedMixin, TimestampedMixin, SoftDeleteMixin, Base):
    """One segment of the wheel — a prize the member can win.

    The draw is **weighted**: a prize's chance is ``weight / sum(weights)`` over
    the prizes still eligible (active, within quota). Set the grand prize to a
    small ``weight`` AND a small ``total_quota`` so it's both rare and capped.

    A "銘謝惠顧 / 沒中" segment is a normal prize row with ``menu_item_id = NULL``
    and ``value_estimate = 0`` — it just doesn't mint a redeemable voucher.
    """

    __tablename__ = "campaign_prizes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Display order around the wheel (left→right / clockwise). Not the odds.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Relative probability weight. 0 = effectively disabled in the draw.
    weight: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # What the winner can redeem. NULL = a "no-win" segment (no voucher minted).
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # TWD value — used to rank "best prize" in the wallet and for ROI reporting.
    value_estimate: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0"), server_default="0"
    )
    # Caps. NULL = unlimited. ``total_quota`` is the whole-campaign ceiling;
    # ``daily_quota`` is a per-day ceiling. ``awarded_count`` is the cached
    # running total (source of truth is the spins/vouchers; cached to avoid an
    # aggregate on every draw).
    total_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awarded_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    campaign: Mapped[MarketingCampaign] = relationship(back_populates="prizes")

    __table_args__ = (
        Index("ix_prizes_campaign_active", "campaign_id", "is_active"),
    )


class CampaignSpin(TenantScopedMixin, Base):
    """Append-only log — one row per spin. Never UPDATE, never DELETE.

    Backs both the audit trail and the "one spin per day" rule (counted by
    ``spin_date``, the Asia/Taipei calendar day of the spin). ``prize_id`` is
    nullable so a no-win spin is still recorded.
    """

    __tablename__ = "campaign_spins"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    prize_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_prizes.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # The Asia/Taipei calendar day this spin counts against (the daily-limit key).
    spin_date: Mapped[date] = mapped_column(Date, nullable=False)
    won: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_spins_campaign_customer_date", "campaign_id", "customer_id", "spin_date"),
        Index("ix_spins_prize_date", "prize_id", "spin_date"),
    )


class CampaignVoucher(TenantScopedMixin, TimestampedMixin, Base):
    """A won prize sitting in the member's wallet, redeemable once.

    Carries snapshots (``prize_name`` / ``value_estimate`` / ``menu_item_id``)
    taken at win-time so the wallet still reads correctly even if the prize
    definition is later edited or soft-deleted.
    """

    __tablename__ = "campaign_vouchers"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketing_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    prize_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_prizes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    spin_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_spins.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Short human-friendly redemption code the counter can type/scan.
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[VoucherStatus] = mapped_column(
        SQLEnum(VoucherStatus, name="voucher_status", native_enum=False, length=16),
        nullable=False,
        default=VoucherStatus.ACTIVE,
        server_default=VoucherStatus.ACTIVE.value,
    )

    # Win-time snapshots.
    prize_name: Mapped[str] = mapped_column(String(120), nullable=False)
    value_estimate: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0"), server_default="0"
    )
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Fixed-offset validity window (dates; inclusive of valid_until).
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)

    # Redemption record.
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redeemed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    redeemed_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    redeemed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "uq_vouchers_tenant_code",
            "tenant_id",
            "code",
            unique=True,
        ),
        Index("ix_vouchers_customer_campaign", "customer_id", "campaign_id"),
        Index("ix_vouchers_campaign_status", "campaign_id", "status"),
        # Backs the "one redemption per visit/day" guard: at most one row per
        # (campaign, customer) with a given redeemed_on date.
        Index(
            "uq_vouchers_one_redeem_per_day",
            "campaign_id",
            "customer_id",
            "redeemed_on",
            unique=True,
            postgresql_where=text("redeemed_on IS NOT NULL"),
        ),
    )


__all__ = [
    "CampaignPrize",
    "CampaignSpin",
    "CampaignStatus",
    "CampaignVoucher",
    "MarketingCampaign",
    "VoucherStatus",
]
