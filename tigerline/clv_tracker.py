"""V3.0 Sprint 4 — Closing Line Value tracker.

Compares the line/price we entered at vs the line/price at market close. The
result is one of:

- ``beat``   — we entered at a better number than closing (line softer for our side)
- ``worse``  — we entered at a worse number than closing
- ``flat``   — line moved within the dead-band (default 0.01)

Long-term ``beat`` percentage is a sharper indicator than win-rate of whether
the system is *actually* selecting value, not just lucky picks.

Pure function + append-only persistence helper.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

CONFIG_DIR = Path(__file__).parent / "config"

CLVResult = Literal["beat", "worse", "flat"]


def _to_decimal(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    return v


YamlDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class CLVConfig(BaseModel):
    """Tunable CLV thresholds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dead_band: YamlDecimal = Field(default=Decimal("0.01"))
    # For backing the favorite, "beat" means we got a less-negative line.
    # For backing the underdog (positive handicap), "beat" means a higher number.
    # We treat ``selection_side="back_favorite"`` vs ``"back_underdog"`` explicitly.


@lru_cache(maxsize=1)
def load_clv_config() -> CLVConfig:
    path = CONFIG_DIR / "clv_rules.yaml"
    if not path.exists():
        return CLVConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return CLVConfig.model_validate(raw)


def reset_clv_cache() -> None:
    load_clv_config.cache_clear()


@dataclass(frozen=True)
class CLVRecord:
    """One entry-vs-close comparison."""

    clv_id: str
    match_id: str
    bookmaker: str
    market_type: str
    selection: str
    entry_line: Decimal | None
    entry_price: Decimal
    entry_at: datetime
    closing_line: Decimal | None
    closing_price: Decimal
    closing_at: datetime
    clv_result: CLVResult
    clv_margin: Decimal
    price_delta: Decimal


def compute_clv(
    *,
    clv_id: str,
    match_id: str,
    bookmaker: str,
    market_type: str,
    selection: str,
    entry_line: Decimal | None,
    entry_price: Decimal,
    entry_at: datetime,
    closing_line: Decimal | None,
    closing_price: Decimal,
    closing_at: datetime,
    selection_side: Literal["back_favorite", "back_underdog"] = "back_favorite",
    config: CLVConfig | None = None,
) -> CLVRecord:
    """Build a CLVRecord from entry vs close.

    ``selection_side`` controls how the line delta is interpreted:
    - back_favorite: entry_line=-1.5, closing_line=-2 → we BEAT close (less negative)
    - back_underdog: entry_line=+1.5, closing_line=+1 → we BEAT close (higher)

    For markets without a line (moneyline/BTTS), only price is compared:
    higher entry_price = beat close.
    """
    cfg = config or load_clv_config()

    price_delta = closing_price - entry_price

    if entry_line is None or closing_line is None:
        # No line — fall back to price comparison.
        if price_delta > cfg.dead_band:
            # Closing price went UP → our entry price was LOWER → we got worse value
            result: CLVResult = "worse"
        elif price_delta < -cfg.dead_band:
            result = "beat"
        else:
            result = "flat"
        margin = abs(price_delta)
    else:
        line_delta = closing_line - entry_line
        if abs(line_delta) <= cfg.dead_band:
            result = "flat"
        elif selection_side == "back_favorite":
            # entry -1.5, close -2 → delta -0.5 → favorite got heavier → we BEAT
            result = "beat" if line_delta < 0 else "worse"
        else:
            # back_underdog: entry +1.5, close +1 → delta -0.5 → underdog line shrank → we BEAT
            result = "beat" if line_delta < 0 else "worse"
        margin = abs(line_delta)

    return CLVRecord(
        clv_id=clv_id,
        match_id=match_id,
        bookmaker=bookmaker,
        market_type=market_type,
        selection=selection,
        entry_line=entry_line,
        entry_price=entry_price,
        entry_at=entry_at,
        closing_line=closing_line,
        closing_price=closing_price,
        closing_at=closing_at,
        clv_result=result,
        clv_margin=margin,
        price_delta=price_delta,
    )


def save_clv(conn: sqlite3.Connection, record: CLVRecord) -> None:
    """Persist a CLV record. Append-only — duplicate clv_id raises IntegrityError."""
    conn.execute(
        "INSERT INTO clv_records ("
        "  clv_id, match_id, bookmaker, market_type, selection, "
        "  entry_line, entry_price, entry_at, "
        "  closing_line, closing_price, closing_at, "
        "  clv_result, clv_margin, price_delta"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.clv_id,
            record.match_id,
            record.bookmaker,
            record.market_type,
            record.selection,
            None if record.entry_line is None else str(record.entry_line),
            str(record.entry_price),
            record.entry_at.isoformat(),
            None if record.closing_line is None else str(record.closing_line),
            str(record.closing_price),
            record.closing_at.isoformat(),
            record.clv_result,
            str(record.clv_margin),
            str(record.price_delta),
        ),
    )
    conn.commit()


def clv_summary(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
) -> dict[str, int | float]:
    """Aggregate hit-rates across all CLV records (optionally since a date)."""
    where = ["1=1"]
    params: list = []
    if since:
        where.append("created_at >= ?")
        params.append(since)
    rows = conn.execute(
        f"SELECT clv_result, COUNT(*) AS n FROM clv_records WHERE {' AND '.join(where)} "
        "GROUP BY clv_result",
        params,
    ).fetchall()
    counts = {row["clv_result"]: row["n"] for row in rows}
    total = sum(counts.values())
    beat = counts.get("beat", 0)
    worse = counts.get("worse", 0)
    flat = counts.get("flat", 0)
    return {
        "total": total,
        "beat": beat,
        "worse": worse,
        "flat": flat,
        "beat_rate": (beat / total) if total else 0.0,
    }


__all__ = [
    "CLVConfig",
    "CLVRecord",
    "CLVResult",
    "clv_summary",
    "compute_clv",
    "load_clv_config",
    "reset_clv_cache",
    "save_clv",
]
