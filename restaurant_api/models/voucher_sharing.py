"""送券裂變 + 貴人配額券 — voucher gifting & VIP quota grants.

Two growth mechanics, one shared wallet/redemption substrate
(see ``docs/18_voucher_sharing_design.md``):

- **模式 A (``VoucherGift``)** — a member shares a voucher via a tokenised link.
  A friend claims it (加好友 → bound as ``recipient``), comes in, and the counter
  redeems it. The reward to the *sender* is granted **only at redemption**, never
  at share time — so the payout cost is incurred only when a real guest eats.
  This mirrors ``referral_service.qualify_referral`` (reward-on-first-spend), the
  anti-farming pattern already proven in this codebase.

- **模式 B (``VoucherGrant``)** — the operator hands a shareholder / 貴人 a capped
  allotment ("$200 抵用券 x20 + 鍋底券 x10 ..."). They distribute to friends who come
  eat. No sender reward (they spend the operator's allotment doing a favour, not
  earning), and ``total_quota`` is a hard ceiling so distribution can't run away.

Reward points ride the existing append-only ``customer_points_ledger`` — no new
points table. Enum columns store the member *name* (``native_enum=False`` +
``server_default=...name``) to match the house style (see ``models/ugc.py``).
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
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    Money,
    TenantScopedMixin,
    TimestampedMixin,
    uuid7,
)


class VoucherGiftStatus(enum.StrEnum):
    """Lifecycle of one member-to-friend gift.

    ``pending``  — share link minted, not yet claimed.
    ``claimed``  — friend added the OA and the voucher landed in their wallet.
    ``redeemed`` — friend redeemed at the counter; **sender reward fires here**.
    ``expired``  — claim window / voucher validity passed unredeemed; terminal.
    ``revoked``  — cancelled by sender or staff; terminal.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    REDEEMED = "redeemed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class VoucherRewardKind(enum.StrEnum):
    """How the sender is rewarded when their gift is redeemed."""

    POINTS = "points"
    CASH_CREDIT = "cash_credit"


class VoucherGift(TenantScopedMixin, TimestampedMixin, Base):
    """A single member-to-friend voucher share (模式 A)."""

    __tablename__ = "voucher_gifts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # The member who sent the gift — reward accrues to them at redemption.
    sender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The friend who claimed it. NULL until claimed; bound to their LINE identity
    # so the same person can't self-send-and-claim to farm rewards.
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The wallet voucher minted for the recipient on claim (NULL until claimed).
    voucher_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_vouchers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Opaque token baked into the share URL; unique per tenant.
    share_token: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[VoucherGiftStatus] = mapped_column(
        SQLEnum(
            VoucherGiftStatus,
            name="voucher_gift_status",
            native_enum=False,
            length=16,
        ),
        nullable=False,
        default=VoucherGiftStatus.PENDING,
        server_default=VoucherGiftStatus.PENDING.name,
    )
    reward_kind: Mapped[VoucherRewardKind] = mapped_column(
        SQLEnum(
            VoucherRewardKind,
            name="voucher_reward_kind",
            native_enum=False,
            length=16,
        ),
        nullable=False,
        default=VoucherRewardKind.POINTS,
        server_default=VoucherRewardKind.POINTS.name,
    )
    # Reward magnitude (points count, or cash-credit amount) settled to the
    # sender when the gift is redeemed. 0 until then.
    reward_amount: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    # When the sender reward was actually settled (one-shot guard: non-NULL ⇒ paid).
    rewarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("uq_voucher_gifts_tenant_token", "tenant_id", "share_token", unique=True),
        Index("ix_voucher_gifts_sender", "sender_id", "status"),
        Index("ix_voucher_gifts_tenant_status", "tenant_id", "status"),
    )


class VoucherGrant(TenantScopedMixin, TimestampedMixin, Base):
    """A capped allotment of vouchers handed to a shareholder / 貴人 (模式 B)."""

    __tablename__ = "voucher_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    # Human name of the grantee (need not be a system member).
    grantee_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Linked customer row when the grantee is also a member (optional).
    grantee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # What kind of voucher this allotment mints: e.g. "cash_credit",
    # "hotpot_base", "meat_platter", "veg_platter". Free-form (String) rather than
    # an enum so the operator can add item kinds without a migration.
    voucher_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Per-voucher face value ($200 抵用) or 0 for item-redemption vouchers.
    face_value: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    # The menu item an item-redemption voucher grants (鍋底 / 肉盤 / 菜盤). NULL for
    # cash-credit vouchers.
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Hard ceiling on how many vouchers this allotment can hand out.
    total_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    # How many have been distributed/redeemed so far (0..total_quota).
    used_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        Index("ix_voucher_grants_tenant", "tenant_id", "created_at"),
    )


__all__ = [
    "VoucherGift",
    "VoucherGiftStatus",
    "VoucherGrant",
    "VoucherRewardKind",
]
