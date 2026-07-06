"""Stake allocator — A+/A/B/C/D → bankroll percentage → stake amount.

The level is picked by the recommender from (classifier confidence + scenario).
The harness adjustment then nudges the % within the level's band: ``upgrade``
clamps to max_pct, ``downgrade`` clamps to min_pct, ``normal`` uses default_pct,
``skip`` zeroes everything regardless of level.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from tigerline.models import Adjustment, StakeLevel
from tigerline.rules import StakeBand, load_stake_bands


def allocate(
    level: StakeLevel,
    bankroll: Decimal,
    adjustment: Adjustment = "normal",
    bands: dict[StakeLevel, StakeBand] | None = None,
) -> tuple[Decimal, Decimal]:
    """Return ``(stake_pct, stake_amount)`` for ``level`` under ``adjustment``.

    The amount is rounded to the nearest whole currency unit (Decimal "1").
    Skipped bets always come back as (0, 0).
    """
    if bands is None:
        bands = load_stake_bands()

    band = bands[level]
    if adjustment == "skip" or level == "D":
        return Decimal("0"), Decimal("0")

    if adjustment == "upgrade":
        pct = band.max_pct
    elif adjustment == "downgrade":
        pct = band.min_pct
    else:
        pct = band.default_pct

    amount = (bankroll * pct).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return pct, amount


def pick_level(scenario_confidence: Decimal, adjustment: Adjustment) -> StakeLevel:
    """Map (confidence, adjustment) → stake level for the main bet.

    The recommender uses this when the classifier produces a real scenario.
    Confidence bands roughly match the rule_confidence heuristic in classifier.py.
    """
    if adjustment == "skip":
        return "D"

    if scenario_confidence >= Decimal("0.85"):
        base: StakeLevel = "A+" if adjustment == "upgrade" else "A"
    elif scenario_confidence >= Decimal("0.55"):
        base = "A" if adjustment == "upgrade" else "B"
    elif scenario_confidence >= Decimal("0.35"):
        base = "B" if adjustment == "upgrade" else "C"
    else:
        base = "C" if adjustment == "upgrade" else "D"

    return base


__all__ = ["allocate", "pick_level"]
