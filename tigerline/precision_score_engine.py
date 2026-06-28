"""V3.0 Sprint 3 — Precision Score Engine.

Produces a 0–100 confidence score by composing eight signal channels per the
spec's §8 weighting:

  TIGER clarity        30
  AH-Totals coherence  15
  Movement support     15
  Consensus            10
  Harness risk         10
  Corridor focus       10
  Price protection      5
  Data completeness     5
  ──────────────────── 100

Each channel returns 0–1; the engine multiplies by weight and adds penalty
deductions from ``config/precision_score_rules.yaml``. The output is a
``PrecisionScore`` with the rolled-up score, per-channel breakdown, applied
penalties, and the recommended stake level (A+/A/B/C/D) per the score bands.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from tigerline.consensus_engine import ConsensusReport
from tigerline.data_quality import confidence_score as metadata_confidence
from tigerline.line_movement_analyzer import MovementReport
from tigerline.models import (
    HarnessVerdict,
    MatchClassification,
    OddsSnapshot,
    ScoreCorridor,
    StakeLevel,
)

CONFIG_DIR = Path(__file__).parent / "config"


def _to_decimal(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    return v


YamlDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class PrecisionWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tiger_clarity: int = 30
    ah_totals_coherence: int = 15
    movement_support: int = 15
    consensus: int = 10
    harness_risk: int = 10
    corridor_focus: int = 10
    price_protection: int = 5
    data_completeness: int = 5


class PrecisionPenalties(BaseModel):
    """Score deductions for missing or contradictory inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    no_multi_book: int = 5
    no_time_series: int = 8
    lineup_unconfirmed: int = 5
    favorite_already_qualified: int = 10
    handicap_totals_conflict: int = 15
    market_disagreement: int = 10
    no_price_protection: int = 20
    juice_too_low: int = 5


class PrecisionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    weights: PrecisionWeights = Field(default_factory=PrecisionWeights)
    penalties: PrecisionPenalties = Field(default_factory=PrecisionPenalties)
    a_plus_min: int = 90
    a_min: int = 80
    b_min: int = 70
    c_min: int = 60


@lru_cache(maxsize=1)
def load_precision_config() -> PrecisionConfig:
    path = CONFIG_DIR / "precision_score_rules.yaml"
    if not path.exists():
        return PrecisionConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PrecisionConfig.model_validate(raw)


def reset_precision_cache() -> None:
    load_precision_config.cache_clear()


@dataclass(frozen=True)
class PrecisionScore:
    """Output of ``score_precision``."""

    score: int  # 0..100
    level: StakeLevel
    channel_scores: dict[str, int]
    penalties_applied: dict[str, int]
    notes: list[str]


def score_precision(
    *,
    classification: MatchClassification,
    corridor: ScoreCorridor,
    harness: HarnessVerdict,
    movement: MovementReport | None,
    consensus: ConsensusReport | None,
    snapshots: list[OddsSnapshot] | None = None,
    risk_flags: list[str] | None = None,
    config: PrecisionConfig | None = None,
) -> PrecisionScore:
    """Roll up signal channels into a single 0..100 score + level."""
    cfg = config or load_precision_config()
    w = cfg.weights
    p = cfg.penalties
    rfs = set(risk_flags or [])
    snaps = snapshots or []

    channels: dict[str, int] = {}

    # TIGER clarity: classifier confidence directly.
    clarity = float(classification.confidence) if classification.scenario != "unclear_skip" else 0
    channels["tiger_clarity"] = round(clarity * w.tiger_clarity)

    # AH-totals coherence: scenarios that pair handicap + totals coherently get full points.
    coherent = classification.scenario in (
        "two_goal_landing",
        "pressure_under",
        "must_win_pressure",
        "narrow_win",
    )
    channels["ah_totals_coherence"] = w.ah_totals_coherence if coherent else 0

    # Movement support: strengthening favorite is a tail-wind for AH backers;
    # stable + low velocity is neutral; weakening cuts the points.
    if movement is None:
        channels["movement_support"] = w.movement_support // 2  # half-credit if unknown
    elif movement.direction == "stable":
        channels["movement_support"] = w.movement_support * 2 // 3
    elif movement.direction == "favorite_strengthening":
        channels["movement_support"] = w.movement_support
    else:
        channels["movement_support"] = w.movement_support // 3

    # Consensus: high agreement + user edge ≥ in_line gets full.
    if consensus is None or consensus.consensus_line is None:
        channels["consensus"] = 0
    else:
        base = w.consensus
        if consensus.agreement == "medium":
            base = base * 3 // 4
        elif consensus.agreement == "low":
            base = base // 2
        if consensus.user_platform_edge == "better_than_market":
            base = min(w.consensus, base + 2)
        elif consensus.user_platform_edge == "worse_than_market":
            base = max(0, base - 4)
        channels["consensus"] = base

    # Harness risk: pass-through if normal, downgrade halves, skip zeros, upgrade full.
    harness_map = {"upgrade": w.harness_risk, "normal": w.harness_risk * 3 // 4,
                   "downgrade": w.harness_risk // 2, "skip": 0}
    channels["harness_risk"] = harness_map[harness.adjustment]

    # Corridor focus: smaller corridor = tighter conviction (but min 1 for sanity).
    corridor_size = max(1, len(corridor.scores))
    channels["corridor_focus"] = max(0, w.corridor_focus - max(0, corridor_size - 3))

    # Price protection: AH-spread scenarios that *back* a protective line (e.g.
    # -1.5 when the real line is -1.75) get the points; raw favorite-juicy doesn't.
    protective_scenarios = {"two_goal_landing", "must_win_pressure", "narrow_win"}
    channels["price_protection"] = w.price_protection if classification.scenario in protective_scenarios else 0

    # Data completeness: average source metadata confidence across snapshots.
    if not snaps:
        channels["data_completeness"] = 0
    else:
        avg_meta = sum(metadata_confidence(s.metadata) for s in snaps) / len(snaps)
        channels["data_completeness"] = round((avg_meta / 100) * w.data_completeness)

    # Penalties: deductions from spec §8.4.
    penalties: dict[str, int] = {}
    if len({s.bookmaker for s in snaps}) < 2:
        penalties["no_multi_book"] = p.no_multi_book
    if len(snaps) < 2:
        penalties["no_time_series"] = p.no_time_series
    if "lineup_unconfirmed" in rfs:
        penalties["lineup_unconfirmed"] = p.lineup_unconfirmed
    if "favorite_already_qualified" in rfs:
        penalties["favorite_already_qualified"] = p.favorite_already_qualified
    if "handicap_totals_conflict" in rfs:
        penalties["handicap_totals_conflict"] = p.handicap_totals_conflict
    if consensus and consensus.agreement == "low":
        penalties["market_disagreement"] = p.market_disagreement
    if classification.scenario not in protective_scenarios | {"goal_market", "pressure_under"}:
        penalties["no_price_protection"] = p.no_price_protection

    raw = sum(channels.values())
    deducted = sum(penalties.values())
    final = max(0, min(100, raw - deducted))

    level = _level_for(final, cfg)
    notes = _build_notes(classification, harness, movement, consensus, channels, penalties)
    return PrecisionScore(score=final, level=level, channel_scores=channels,
                          penalties_applied=penalties, notes=notes)


def _level_for(score: int, cfg: PrecisionConfig) -> StakeLevel:
    if score >= cfg.a_plus_min:
        return "A+"
    if score >= cfg.a_min:
        return "A"
    if score >= cfg.b_min:
        return "B"
    if score >= cfg.c_min:
        return "C"
    return "D"


def _build_notes(
    classification: MatchClassification,
    harness: HarnessVerdict,
    movement: MovementReport | None,
    consensus: ConsensusReport | None,
    channels: dict[str, int],
    penalties: dict[str, int],
) -> list[str]:
    notes: list[str] = []
    if movement and movement.direction == "favorite_strengthening":
        notes.append(f"Movement: line {movement.opening_line} → {movement.current_line} (strengthening).")
    if movement and movement.direction == "favorite_weakening":
        notes.append(f"Movement: line {movement.opening_line} → {movement.current_line} (weakening — caution).")
    if consensus and consensus.user_platform_edge == "better_than_market":
        notes.append(f"User book {consensus.user_platform_line} beats consensus {consensus.consensus_line}.")
    if "no_time_series" in penalties:
        notes.append("Only one snapshot recorded — no movement signal available.")
    if harness.adjustment == "skip":
        notes.append("Harness verdict: SKIP — overrides any score.")
    return notes


__all__ = [
    "PrecisionConfig",
    "PrecisionPenalties",
    "PrecisionScore",
    "PrecisionWeights",
    "load_precision_config",
    "reset_precision_cache",
    "score_precision",
]
