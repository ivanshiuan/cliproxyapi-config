"""TIGER LINE PRIME — Pydantic v2 data model.

All inputs/outputs of the decision pipeline live here. Conventions match
``restaurant_api/schemas/orders.py``:

- Models are ``frozen=True`` so a ``MatchInput`` cannot be mutated mid-pipeline.
- Money & line fields use ``StrictDecimal`` (rejects bare ``float``) — we don't
  use model-wide ``strict=True`` because that breaks JSON string → Decimal
  coercion (e.g. ``"-1.75"`` reaching the parser as a JSON number).
- Scenario / stake-level types are ``Literal`` enums so the classifier and the
  recommender share one source of truth.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# ──────────────────────────────────────────────────────────────────────────
# Primitive aliases
# ──────────────────────────────────────────────────────────────────────────


def _reject_float(value: Any) -> Any:
    """Pre-validator: reject bare ``float`` literals on Decimal fields.

    Pydantic's default coercion would happily turn ``-1.75`` (a float) into
    ``Decimal("-1.7499999...")``. That's wrong for handicap / totals lines and
    for money. Strings, ints and Decimals pass through unchanged.
    """
    if isinstance(value, float):
        raise ValueError(
            "float literals are not accepted for line/money fields; use a string or Decimal"
        )
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]

# ──────────────────────────────────────────────────────────────────────────
# Enums (Literal aliases)
# ──────────────────────────────────────────────────────────────────────────

ScenarioType = Literal[
    "two_goal_landing",
    "must_win_pressure",
    "pressure_under",
    "goal_market",
    "narrow_win",
    "rotation_trap",
    "unclear_skip",
]

StakeLevel = Literal["A+", "A", "B", "C", "D"]

MainResult = Literal["win", "half_win", "push", "half_lose", "lose", "void", "skipped"]

TeamNeedEnum = Literal["must_win", "draw_ok", "rotation", "neutral", "unknown"]

Side = Literal["home", "away"]

MarketKind = Literal["AH", "totals", "correct_score", "BTTS"]

Adjustment = Literal["upgrade", "normal", "downgrade", "skip"]

# V3.0 — Snapshot collector vocabulary.
# ``OddsFormat`` is the wire format of ``price``; the snapshot collector keeps the
# original ``odds_format`` so downstream consumers can normalise without losing
# precision. Internal computations should still use decimal/normalised odds, but
# we preserve the input form for audit.
OddsFormat = Literal["decimal", "hk", "malay", "indo", "american"]
MarketType = Literal[
    "AH_full",
    "AH_first_half",
    "totals_full",
    "totals_first_half",
    "moneyline",
    "BTTS",
    "correct_score",
]
SourceKind = Literal["manual", "api", "screenshot", "system"]
SourceConfidence = Literal["high", "medium", "low"]

# ──────────────────────────────────────────────────────────────────────────
# Input — what the user (or tiger-pm) hands to ``tiger analyze``
# ──────────────────────────────────────────────────────────────────────────


class AsianHandicap(BaseModel):
    """One Asian-handicap line.

    ``line`` is the home-side notation: -1.75 means home -1.5/2 (favored).
    Splits (``-1.5/2`` etc.) are stored as their midpoint (here -1.75).
    """

    model_config = ConfigDict(frozen=True)

    line: StrictDecimal
    favorite: Side
    home_price: StrictDecimal = Field(gt=Decimal("1"))
    away_price: StrictDecimal = Field(gt=Decimal("1"))


class Totals(BaseModel):
    """One Asian totals line (goal market)."""

    model_config = ConfigDict(frozen=True)

    line: StrictDecimal = Field(gt=Decimal("0"))
    over_price: StrictDecimal = Field(gt=Decimal("1"))
    under_price: StrictDecimal = Field(gt=Decimal("1"))


class TeamNeed(BaseModel):
    """What each side needs from this match.

    The classifier uses this aggressively — ``rotation`` on the favorite is the
    single highest-priority signal (short-circuits to ``rotation_trap``).
    """

    model_config = ConfigDict(frozen=True)

    home: TeamNeedEnum
    away: TeamNeedEnum


class GroupContext(BaseModel):
    """Standings context — drives ``final_round`` and ``same_time_kickoff`` rules."""

    model_config = ConfigDict(frozen=True)

    standings_summary: str = Field(max_length=500)
    final_round: bool = False
    same_time_kickoff: bool = False


class MatchInput(BaseModel):
    """Everything ``tiger analyze`` needs to produce a BetPlan."""

    model_config = ConfigDict(frozen=True)

    match_id: str = Field(min_length=1, pattern=r"^[a-z0-9\-]+$")
    kickoff_utc: datetime
    home: str = Field(min_length=1)
    away: str = Field(min_length=1)
    group_context: GroupContext
    team_need: TeamNeed
    market: AsianHandicap
    totals: Totals
    bankroll: StrictDecimal = Field(gt=Decimal("0"))
    risk_flags: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Pipeline outputs
# ──────────────────────────────────────────────────────────────────────────


class MatchClassification(BaseModel):
    """Output of the classifier — one of 7 scenarios + confidence + reasons."""

    model_config = ConfigDict(frozen=True)

    scenario: ScenarioType
    confidence: StrictDecimal = Field(ge=Decimal("0"), le=Decimal("1"))
    reasons: list[str] = Field(default_factory=list)


class ScoreCorridor(BaseModel):
    """3-5 candidate scores; ``primary`` is the model's single best guess.

    ``scores`` stores ``(home_goals, away_goals)`` tuples. ``primary`` MUST be
    inside ``scores`` (validated by ``_validate_primary_in_corridor`` in the
    corridor module).
    """

    model_config = ConfigDict(frozen=True)

    scores: list[tuple[int, int]] = Field(min_length=1, max_length=8)
    primary: tuple[int, int]


class HarnessVerdict(BaseModel):
    """Risk-control decision: upgrade / normal / downgrade / skip."""

    model_config = ConfigDict(frozen=True)

    adjustment: Adjustment
    notes: list[str] = Field(default_factory=list)


class BetLeg(BaseModel):
    """A single recommended bet."""

    model_config = ConfigDict(frozen=True)

    market: MarketKind
    selection: str = Field(min_length=1)
    level: StakeLevel
    stake_pct: StrictDecimal = Field(ge=Decimal("0"), le=Decimal("0.30"))
    stake_amount: StrictDecimal = Field(ge=Decimal("0"))


class BetPlan(BaseModel):
    """Final deliverable: main bet, secondary legs, correct-score sprinkles,
    avoid list, and what to reserve for in-play action.
    """

    model_config = ConfigDict(frozen=True)

    match_id: str
    scenario: ScenarioType
    main_bet: BetLeg | None
    secondary: list[BetLeg] = Field(default_factory=list)
    correct_score: list[BetLeg] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    reserve_live: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    """Post-match scorecard — 0-100 + suggested rule fixes."""

    model_config = ConfigDict(frozen=True)

    match_id: str
    actual_score: tuple[int, int]
    scenario_correct: bool
    corridor_hit: bool
    main_result: MainResult
    score: int = Field(ge=0, le=100)
    suggestions: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# V3.0 — Snapshot collector + source metadata (Sprint 1)
# ──────────────────────────────────────────────────────────────────────────


class SourceMetadata(BaseModel):
    """Provenance for every line/snapshot landing in the system.

    Every snapshot — manual entry, API pull, screenshot OCR, or system-generated
    consensus — must declare where it came from, when it was observed, and how
    confident the collector is in the value. Downstream engines (movement,
    consensus, precision) refuse to score snapshots that lack this envelope.
    """

    model_config = ConfigDict(frozen=True)

    source: SourceKind
    collected_at: datetime
    source_confidence: SourceConfidence
    is_verified: bool = False
    notes: str = Field(default="", max_length=500)


class OddsSnapshot(BaseModel):
    """One observed line at one moment from one book.

    The collector writes these append-only — never UPDATE, never DELETE. The
    full history is the foundation for line movement, consensus, and CLV.

    ``line`` is None for markets without a numeric handicap (moneyline, BTTS).
    For correct_score the ``selection`` carries the score (e.g. "2-0") and
    ``line`` is None.
    """

    model_config = ConfigDict(frozen=True)

    snapshot_id: str = Field(min_length=1, pattern=r"^[a-z0-9\-_]+$")
    match_id: str = Field(min_length=1, pattern=r"^[a-z0-9\-]+$")
    bookmaker: str = Field(min_length=1, max_length=64)
    market_type: MarketType
    selection: str = Field(min_length=1, max_length=64)
    line: StrictDecimal | None = None
    price: StrictDecimal = Field(gt=Decimal("1"))
    odds_format: OddsFormat
    metadata: SourceMetadata
