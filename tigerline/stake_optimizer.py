"""V3.0 Sprint 3 — Stake Optimizer.

Wraps the V2.0 ``tigerline.stake.allocate`` with V3.0 exposure controls:

- Per-match cap (drives off the level band — same as V2.0).
- Daily exposure cap (% of bankroll across all live picks).
- Per-scenario cap (e.g. no more than 40% of bankroll on rotation-trap picks
  for the day).
- Correlation control: if two picks share the same favorite team **and** the
  same direction (both back the favorite to win, both back over, etc.), they
  count as correlated and share a cap.
- Half-Kelly remains opt-in via ``enable_half_kelly=True``; default off.

The optimizer is a pure function: caller passes the bankroll and a list of
``Pick`` candidates; it returns an ``OptimizedAllocation`` with the chosen
stake per pick + the cuts it made and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from tigerline.models import Adjustment, MarketKind, ScenarioType, StakeLevel
from tigerline.stake import allocate as v2_allocate

CONFIG_DIR = Path(__file__).parent / "config"


def _to_decimal(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    return v


YamlDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class ExposureCaps(BaseModel):
    """Tunable exposure caps as fractions of bankroll."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    daily_total: YamlDecimal = Field(default=Decimal("0.45"))  # 45% of bankroll max per day
    per_scenario: YamlDecimal = Field(default=Decimal("0.30"))  # 30% per scenario family
    per_correlation_group: YamlDecimal = Field(default=Decimal("0.25"))  # 25% per correlated cluster
    correct_score_total: YamlDecimal = Field(default=Decimal("0.12"))
    parlay_total: YamlDecimal = Field(default=Decimal("0.08"))


@lru_cache(maxsize=1)
def load_exposure_caps() -> ExposureCaps:
    path = CONFIG_DIR / "exposure_rules.yaml"
    if not path.exists():
        return ExposureCaps()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ExposureCaps.model_validate(raw)


def reset_exposure_cache() -> None:
    load_exposure_caps.cache_clear()


@dataclass(frozen=True)
class Pick:
    """One candidate pick handed to the optimizer."""

    pick_id: str
    match_id: str
    scenario: ScenarioType
    market: MarketKind
    selection: str
    level: StakeLevel
    adjustment: Adjustment
    favorite_team: str | None  # used for correlation grouping; None for totals


@dataclass(frozen=True)
class AllocatedPick:
    """One pick after the optimizer assigns a stake."""

    pick: Pick
    requested_pct: Decimal
    final_pct: Decimal
    final_amount: Decimal
    cut_reason: str | None  # None if uncut


@dataclass(frozen=True)
class OptimizedAllocation:
    """Optimizer output."""

    bankroll: Decimal
    picks: list[AllocatedPick]
    total_pct: Decimal
    total_amount: Decimal
    cuts: list[str] = field(default_factory=list)


def optimize_stakes(
    bankroll: Decimal,
    picks: list[Pick],
    *,
    caps: ExposureCaps | None = None,
    enable_half_kelly: bool = False,
) -> OptimizedAllocation:
    """Apply per-match → per-scenario → daily → correlation caps in order."""
    c = caps or load_exposure_caps()
    cuts: list[str] = []

    # Step 1: V2.0 per-match allocation for each pick.
    requested: list[tuple[Pick, Decimal, Decimal]] = []  # (pick, pct, amount)
    for p in picks:
        pct, amount = v2_allocate(p.level, bankroll, p.adjustment)
        if enable_half_kelly:
            pct = pct / Decimal("2")
            amount = (bankroll * pct).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        requested.append((p, pct, amount))

    # Step 2: per-scenario cap.
    scenario_totals: dict[ScenarioType, Decimal] = {}
    after_scenario: list[tuple[Pick, Decimal, Decimal, str | None]] = []
    for pick, pct, amount in requested:
        running = scenario_totals.get(pick.scenario, Decimal("0"))
        if running + pct > c.per_scenario:
            allowed = max(Decimal("0"), c.per_scenario - running)
            new_amount = (bankroll * allowed).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            cuts.append(
                f"{pick.pick_id}: scenario cap {pick.scenario} → cut from {pct} to {allowed}"
            )
            after_scenario.append((pick, allowed, new_amount, "scenario_cap"))
            scenario_totals[pick.scenario] = running + allowed
        else:
            after_scenario.append((pick, pct, amount, None))
            scenario_totals[pick.scenario] = running + pct

    # Step 3: correlation cap. Group by (favorite_team, market_direction).
    corr_totals: dict[tuple[str | None, str], Decimal] = {}
    after_corr: list[tuple[Pick, Decimal, Decimal, str | None]] = []
    for pick, pct, amount, reason in after_scenario:
        key = (pick.favorite_team, pick.market)
        running = corr_totals.get(key, Decimal("0"))
        if running + pct > c.per_correlation_group:
            allowed = max(Decimal("0"), c.per_correlation_group - running)
            new_amount = (bankroll * allowed).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            cuts.append(
                f"{pick.pick_id}: correlation cap {pick.favorite_team}/{pick.market} "
                f"→ cut from {pct} to {allowed}"
            )
            after_corr.append((pick, allowed, new_amount, reason or "correlation_cap"))
            corr_totals[key] = running + allowed
        else:
            after_corr.append((pick, pct, amount, reason))
            corr_totals[key] = running + pct

    # Step 4: daily total cap.
    daily_running = Decimal("0")
    after_daily: list[AllocatedPick] = []
    for pick, pct, amount, reason in after_corr:
        if daily_running + pct > c.daily_total:
            allowed = max(Decimal("0"), c.daily_total - daily_running)
            new_amount = (bankroll * allowed).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            cuts.append(
                f"{pick.pick_id}: daily cap → cut from {pct} to {allowed}"
            )
            after_daily.append(
                AllocatedPick(
                    pick=pick,
                    requested_pct=_requested_pct(pick, bankroll, enable_half_kelly),
                    final_pct=allowed,
                    final_amount=new_amount,
                    cut_reason=reason or "daily_cap",
                )
            )
            daily_running += allowed
        else:
            after_daily.append(
                AllocatedPick(
                    pick=pick,
                    requested_pct=_requested_pct(pick, bankroll, enable_half_kelly),
                    final_pct=pct,
                    final_amount=amount,
                    cut_reason=reason,
                )
            )
            daily_running += pct

    total_pct = sum((ap.final_pct for ap in after_daily), Decimal("0"))
    total_amount = sum((ap.final_amount for ap in after_daily), Decimal("0"))
    return OptimizedAllocation(
        bankroll=bankroll,
        picks=after_daily,
        total_pct=total_pct,
        total_amount=total_amount,
        cuts=cuts,
    )


def _requested_pct(pick: Pick, bankroll: Decimal, half_kelly: bool) -> Decimal:
    pct, _ = v2_allocate(pick.level, bankroll, pick.adjustment)
    return pct / Decimal("2") if half_kelly else pct


__all__ = [
    "AllocatedPick",
    "ExposureCaps",
    "OptimizedAllocation",
    "Pick",
    "load_exposure_caps",
    "optimize_stakes",
    "reset_exposure_cache",
]
