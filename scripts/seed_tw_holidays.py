"""Seed Taiwan public holidays into ``public_holidays``.

Run me once per calendar year — list the official Executive Yuan
公務人員行事曆 days under ``HOLIDAYS_2026`` (and successor lists for
later years). Re-running is idempotent thanks to the unique
``(country, holiday_date)`` index.

Source of truth for the dates lives here — keep this file in sync
with the Executive Yuan announcement. Lunar-calendar holidays
(春節 / 端午 / 中秋) shift every year so updating this list IS the
yearly compliance ritual.

Usage::

    python scripts/seed_tw_holidays.py            # seeds every known year
    python scripts/seed_tw_holidays.py --year 2026
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from sqlalchemy.dialects.postgresql import insert as pg_insert

from restaurant_api.database import get_sessionmaker
from restaurant_api.models import PublicHoliday

logger = logging.getLogger("scripts.seed_tw_holidays")


# Fixed-date national holidays — same date every year.
_FIXED: list[tuple[int, int, str]] = [
    (1, 1, "中華民國開國紀念日"),
    (2, 28, "和平紀念日"),
    (4, 4, "兒童節"),
    (4, 5, "民族掃墓節"),
    (5, 1, "勞動節"),
    (10, 10, "國慶日"),
]


# Lunar / Executive-Yuan announced dates per year. Verify against the
# official 行政院人事行政總處 calendar before each new year.
_LUNAR_BY_YEAR: dict[int, list[tuple[int, int, str]]] = {
    2026: [
        (2, 16, "農曆除夕"),
        (2, 17, "春節"),
        (2, 18, "春節"),
        (2, 19, "春節"),
        (6, 19, "端午節"),
        (9, 25, "中秋節"),
    ],
    2027: [
        # TODO: confirm against Executive Yuan announcement before 2026 Q4.
        (2, 5, "農曆除夕"),
        (2, 6, "春節"),
        (2, 7, "春節"),
        (2, 8, "春節"),
        (6, 9, "端午節"),
        (9, 15, "中秋節"),
    ],
}


def _rows_for_year(year: int) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m, d, name in _FIXED:
        out.append(
            {"country": "TW", "holiday_date": date(year, m, d), "name": name}
        )
    for m, d, name in _LUNAR_BY_YEAR.get(year, []):
        out.append(
            {"country": "TW", "holiday_date": date(year, m, d), "name": name}
        )
    return out


async def _seed_rows(rows: list[dict[str, object]]) -> int:
    SessionLocal = get_sessionmaker()
    async with SessionLocal() as session:
        stmt = pg_insert(PublicHoliday).values(rows)
        # On duplicate (country, holiday_date) — do nothing; keeps re-runs safe.
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["country", "holiday_date"]
        )
        await session.execute(stmt)
        await session.commit()
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year",
        type=int,
        action="append",
        help="One year to seed; can be repeated. Default: every known year.",
    )
    args = parser.parse_args()

    years = args.year or sorted(set([*_LUNAR_BY_YEAR.keys()]))
    all_rows: list[dict[str, object]] = []
    for y in years:
        all_rows.extend(_rows_for_year(y))

    if not all_rows:
        print(f"No rows to seed for years {years}.")
        return 1

    n = asyncio.run(_seed_rows(all_rows))
    print(f"✅ Seeded {n} TW public-holiday rows across years {years}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
