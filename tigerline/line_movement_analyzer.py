"""V3.0 Sprint 2 — Line Movement Engine.

Reads odds snapshots (collected by Sprint 1) for one (match, market) pair and
computes the time-series signals downstream engines need:

- line_delta / price_delta
- velocity (delta per hour)
- direction (favorite_strengthening / favorite_weakening / stable)
- steam_move (sharp uniform move across books)
- reverse_line_movement (line moves against the public bet)
- crosses_key_handicap (whether movement crossed a sharp threshold like -1, -2)

All thresholds come from ``config/movement_rules.yaml`` so Ivan can tune.
Pure function — no I/O, no SQLite reads (caller passes snapshots in).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from tigerline.models import OddsSnapshot

Direction = Literal["favorite_strengthening", "favorite_weakening", "stable"]
Velocity = Literal["fast", "moderate", "slow", "none"]


def _to_decimal(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    return v


YamlDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]

CONFIG_DIR = Path(__file__).parent / "config"


class MovementThresholds(BaseModel):
    """Tunable movement thresholds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fast_velocity_per_hour: YamlDecimal = Field(default=Decimal("0.25"))
    moderate_velocity_per_hour: YamlDecimal = Field(default=Decimal("0.1"))
    steam_min_line_delta: YamlDecimal = Field(default=Decimal("0.25"))
    steam_min_books: int = Field(default=2, ge=1)
    stable_band: YamlDecimal = Field(default=Decimal("0.05"))
    key_handicaps: list[YamlDecimal] = Field(
        default_factory=lambda: [
            Decimal("-2.5"),
            Decimal("-2"),
            Decimal("-1.5"),
            Decimal("-1"),
            Decimal("-0.5"),
        ]
    )


@lru_cache(maxsize=1)
def load_movement_rules() -> MovementThresholds:
    path = CONFIG_DIR / "movement_rules.yaml"
    if not path.exists():
        return MovementThresholds()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # The same file also carries a ``consensus`` block for the consensus engine;
    # strip it before validating against the movement schema.
    movement_only = {k: v for k, v in raw.items() if k != "consensus"}
    return MovementThresholds.model_validate(movement_only)


def reset_movement_cache() -> None:
    load_movement_rules.cache_clear()


@dataclass(frozen=True)
class MovementReport:
    """Output of ``analyze_movement``."""

    match_id: str
    market_type: str
    bookmaker: str | None  # None when aggregating across books
    opening_line: Decimal | None
    current_line: Decimal | None
    opening_price: Decimal
    current_price: Decimal
    line_delta: Decimal
    price_delta: Decimal
    velocity: Velocity
    direction: Direction
    crosses_key_handicaps: list[Decimal]
    snapshots_used: int


def analyze_movement(
    snapshots: list[OddsSnapshot],
    *,
    thresholds: MovementThresholds | None = None,
) -> MovementReport | None:
    """Compute a movement report from at least 2 snapshots, sorted by time.

    Returns ``None`` when fewer than 2 snapshots are provided.
    """
    if len(snapshots) < 2:
        return None

    t = thresholds or load_movement_rules()
    ordered = sorted(snapshots, key=lambda s: s.metadata.collected_at)
    first, last = ordered[0], ordered[-1]

    line_delta = _safe_sub(last.line, first.line)
    price_delta = last.price - first.price

    hours = max(
        (last.metadata.collected_at - first.metadata.collected_at).total_seconds() / 3600.0,
        0.001,
    )
    abs_per_hour = abs(line_delta) / Decimal(str(hours)) if line_delta else Decimal("0")

    velocity: Velocity
    if line_delta == 0:
        velocity = "none"
    elif abs_per_hour >= t.fast_velocity_per_hour:
        velocity = "fast"
    elif abs_per_hour >= t.moderate_velocity_per_hour:
        velocity = "moderate"
    else:
        velocity = "slow"

    direction: Direction = "stable"
    if abs(line_delta) > t.stable_band:
        # More negative line = favorite getting heavier handicap = strengthening
        direction = "favorite_strengthening" if line_delta < 0 else "favorite_weakening"

    crosses = _crosses_key(first.line, last.line, t.key_handicaps)

    return MovementReport(
        match_id=first.match_id,
        market_type=first.market_type,
        bookmaker=first.bookmaker if all(s.bookmaker == first.bookmaker for s in ordered) else None,
        opening_line=first.line,
        current_line=last.line,
        opening_price=first.price,
        current_price=last.price,
        line_delta=line_delta,
        price_delta=price_delta,
        velocity=velocity,
        direction=direction,
        crosses_key_handicaps=crosses,
        snapshots_used=len(ordered),
    )


def detect_steam_move(
    per_book_reports: list[MovementReport],
    thresholds: MovementThresholds | None = None,
) -> bool:
    """A steam move = ≥N books moved the line in the same direction by ≥threshold."""
    if len(per_book_reports) < 2:
        return False
    t = thresholds or load_movement_rules()

    same_direction = [r for r in per_book_reports if r.direction != "stable"]
    if len(same_direction) < t.steam_min_books:
        return False
    directions = {r.direction for r in same_direction}
    if len(directions) > 1:
        return False
    qualifying = [r for r in same_direction if abs(r.line_delta) >= t.steam_min_line_delta]
    return len(qualifying) >= t.steam_min_books


def detect_reverse_line_movement(
    report: MovementReport,
    *,
    public_side: Literal["home", "away"],
    favorite_side: Literal["home", "away"],
) -> bool:
    """RLM = public on side X, but line moves AGAINST side X.

    If public bets the favorite and line moves favorite_weakening → RLM.
    If public bets the underdog and line moves favorite_strengthening → RLM.
    """
    if report.direction == "stable":
        return False
    if public_side == favorite_side:
        return report.direction == "favorite_weakening"
    return report.direction == "favorite_strengthening"


def _safe_sub(a: Decimal | None, b: Decimal | None) -> Decimal:
    if a is None or b is None:
        return Decimal("0")
    return a - b


def _crosses_key(
    first: Decimal | None,
    last: Decimal | None,
    keys: list[Decimal],
) -> list[Decimal]:
    """Which key handicaps the line moved across (either direction)."""
    if first is None or last is None or first == last:
        return []
    lo, hi = min(first, last), max(first, last)
    return sorted(k for k in keys if lo < k < hi or (lo == k != last))


__all__ = [
    "Direction",
    "MovementReport",
    "MovementThresholds",
    "Velocity",
    "analyze_movement",
    "detect_reverse_line_movement",
    "detect_steam_move",
    "load_movement_rules",
    "reset_movement_cache",
]
