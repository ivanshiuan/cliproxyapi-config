"""Shared SQLAlchemy 2.x infrastructure for the restaurant SaaS backend.

Holds the declarative ``Base``, the three cross-cutting mixins
(``TimestampedMixin``, ``SoftDeleteMixin``, ``TenantScopedMixin``), the
``Money`` type alias, and a small UUIDv7 generator used as the default for
every primary key.

PostgreSQL 16 does not ship a native ``uuidv7()`` function (that arrives in
PG18), and we explicitly do not want to pull in a new runtime dependency. So
we generate UUIDv7 in Python — the layout follows RFC 9562 §5.7:

    unix_ts_ms (48 bits) | ver=0x7 (4 bits) | rand_a (12 bits)
                          | var=0b10 (2 bits) | rand_b (62 bits)

The resulting UUIDs are time-ordered, which preserves B-tree locality on
``id`` columns and gives us chronological sort for free.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# UUIDv7
# ---------------------------------------------------------------------------


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (RFC 9562 §5.7).

    Time-ordered 128-bit identifier: 48 bits of unix-millis followed by
    version/variant bits and 74 bits of cryptographic randomness. Used as the
    default for every ``id`` column in this schema; matches the architectural
    decision documented in ``docs/04_data_schema.md``.
    """
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    rand_a = (rand >> 64) & 0x0FFF  # 12 bits
    rand_b = rand & 0x3FFFFFFFFFFFFFFF  # 62 bits
    value = (unix_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76  # version
    value |= rand_a << 64
    value |= 0b10 << 62  # variant (RFC 4122)
    value |= rand_b
    return uuid.UUID(int=value)


# ---------------------------------------------------------------------------
# Money type alias
# ---------------------------------------------------------------------------

# 14 digits / 4 dp — see ADR #4 in docs/04_data_schema.md. TWD has no
# decimals at the till, but BOM unit costs (e.g. 0.0125 TWD per gram of salt)
# need 4 dp precision.
Money = Numeric(14, 4)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy 2.x declarative base.

    All ORM models inherit from this. We deliberately keep the base
    minimal — cross-cutting concerns live in the mixins below so they can
    be opted into per table.
    """


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class TimestampedMixin:
    """Adds ``created_at`` / ``updated_at`` to a model.

    Both are timezone-aware (``timestamptz``) and default to ``now()`` at the
    DB level. ``updated_at`` is also kept current by the ``trg_touch_updated_at``
    Postgres trigger documented in the schema doc — we mirror that with
    ``onupdate=func.now()`` so SQLAlchemy-side updates also bump it.
    """

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


class SoftDeleteMixin:
    """Adds a nullable ``deleted_at`` column.

    Used for user-facing records (menu items, employees, stores, etc.) where
    operators want an "oops undelete" path. Financial events (orders,
    stock_movements, payments) are hard-immutable instead — they get reversal
    entries.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TenantScopedMixin:
    """Adds ``tenant_id`` FK to ``tenants(id)`` with RESTRICT.

    Every business table carries this — it's the partition key for
    multi-tenancy and the column the RLS policies key off of. RESTRICT on
    delete because we don't want a tenant accidentally cascade-wiping its
    own books.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


__all__ = [
    "Base",
    "Money",
    "SoftDeleteMixin",
    "TenantScopedMixin",
    "TimestampedMixin",
    "uuid7",
]
