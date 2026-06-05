"""In-memory Taiwan public-holiday calendar.

The clock service classifies hours with a synchronous ``IsHolidayFn``
(``Callable[[date], bool]``) — Pydantic-validated, dependency-injected.
A real DB lookup per call would be async and would also dominate the
hot path (every clock-out hits it), so we cache the holiday set in
memory and refresh it lazily.

The cache is process-local. For a multi-worker deploy each worker
warms its own copy on first request; a 30-row YEAR-set is tiny so the
extra reads at startup are noise.

Contract:
    cal = TaiwanHolidayCalendar(session_factory)
    await cal.refresh()  # optional eager prime; otherwise auto-warms
    cal.is_holiday(date(2026, 1, 1))  # True for 元旦
    cal.is_holiday(date(2026, 6, 6))  # True (weekend fallback)

The "weekend fallback" preserves the prior MVP behavior — Saturdays and
Sundays count as holidays even when the table is empty (e.g. on a
fresh deploy that hasn't been seeded). This is the safe direction:
mis-tagging a weekday as a holiday over-pays employees; missing a
holiday under-pays them — and Taiwan 勞動部 levies fines for the
under-pay case.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..models import PublicHoliday

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger("restaurant_api.services.holiday_calendar")

_DEFAULT_COUNTRY = "TW"


class TaiwanHolidayCalendar:
    """Cached lookup over the ``public_holidays`` table."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession]
        | async_sessionmaker[AsyncSession]
        | None = None,
        *,
        country: str = _DEFAULT_COUNTRY,
        weekend_fallback: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._country = country
        self._weekend_fallback = weekend_fallback
        # None ≠ empty: None means "never refreshed", empty set means
        # "refreshed and the DB has no rows for this country".
        self._dates: set[date] | None = None

    async def refresh(self) -> int:
        """Re-pull the holiday set from the DB. Returns the row count."""
        if self._session_factory is None:
            # Detached mode (used by tests with seed_dates=); refresh is a no-op.
            return len(self._dates or set())

        async with self._session_factory() as session:  # type: ignore[misc]
            stmt = select(PublicHoliday.holiday_date).where(
                PublicHoliday.country == self._country
            )
            rows = (await session.execute(stmt)).scalars().all()
            self._dates = set(rows)
        logger.info(
            "holiday_calendar.refreshed",
            extra={"country": self._country, "count": len(self._dates)},
        )
        return len(self._dates)

    def seed_dates(self, dates: set[date]) -> None:
        """Inject a pre-built set (test path; bypasses the DB)."""
        self._dates = set(dates)

    def is_holiday(self, d: date) -> bool:
        """Synchronous predicate used by ``classify_hours``.

        Returns True when:
          - the date is in the cached national-holiday set, OR
          - ``weekend_fallback=True`` (default) AND the date is Sat/Sun.

        Lazy-refresh is deliberately NOT done here — the cache may not
        be primed yet but ``is_holiday`` must stay sync. Callers wire
        ``refresh()`` into FastAPI's lifespan startup.
        """
        if self._dates is not None and d in self._dates:
            return True
        return self._weekend_fallback and d.weekday() in (5, 6)


# ──────────────────────────────────────────────────────────────────────────
# Module singleton
# ──────────────────────────────────────────────────────────────────────────


_singleton: TaiwanHolidayCalendar | None = None


def get_calendar() -> TaiwanHolidayCalendar:
    """Return the process-wide calendar (lazily constructed)."""
    global _singleton
    if _singleton is None:
        # Wire the DB-backed session factory lazily — importing it eagerly at
        # module load would force ``database.py`` to initialise its engine
        # before settings are applied.
        from ..database import get_sessionmaker

        _singleton = TaiwanHolidayCalendar(get_sessionmaker())
    return _singleton


async def refresh_singleton() -> int:
    """Prime the singleton from the DB. Call from app lifespan startup."""
    return await get_calendar().refresh()


def reset_singleton_for_tests() -> None:
    """Drop the cached instance — only used by tests that swap the DB out."""
    global _singleton
    _singleton = None


__all__ = [
    "TaiwanHolidayCalendar",
    "get_calendar",
    "refresh_singleton",
    "reset_singleton_for_tests",
]
