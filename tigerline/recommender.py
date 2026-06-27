"""Recommender — composes the full BetPlan from the classifier / corridor / harness / stake.

This is the public entry point for ``tiger analyze``. Inputs flow:

    MatchInput
      → classify(...)        → MatchClassification
      → build_corridor(...)  → ScoreCorridor
      → harness.evaluate(...) → HarnessVerdict
      → pick_level + allocate → main BetLeg
      → recommend(...)       → BetPlan

The recommender holds the scenario-to-market mapping (which AH selection or
totals line to actually bet on) and the avoid / reserve-live copy.
"""

from __future__ import annotations

from decimal import Decimal

from tigerline.classifier import classify
from tigerline.corridor import build_corridor
from tigerline.harness import evaluate as evaluate_harness
from tigerline.models import (
    BetLeg,
    BetPlan,
    HarnessVerdict,
    MatchClassification,
    MatchInput,
    ScenarioType,
    ScoreCorridor,
)
from tigerline.stake import allocate, pick_level

_CORRECT_SCORE_LEVEL = "C"
_CORRECT_SCORE_PCT_BUDGET = Decimal("0.04")  # total over all CS legs


def recommend(match: MatchInput) -> BetPlan:
    """Run the full pipeline and produce a BetPlan."""
    classification = classify(match)
    corridor = build_corridor(classification.scenario, match)
    verdict = evaluate_harness(match, classification)

    main_bet = _build_main_bet(match, classification, verdict)
    secondary = _build_secondary(match, classification, verdict)
    correct_score = _build_correct_score(match, corridor, verdict)
    avoid = _build_avoid(match, classification)
    reserve_live = _build_reserve_live(classification)

    return BetPlan(
        match_id=match.match_id,
        scenario=classification.scenario,
        main_bet=main_bet,
        secondary=secondary,
        correct_score=correct_score,
        avoid=avoid,
        reserve_live=reserve_live,
    )


# ──────────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────────


def _build_main_bet(
    match: MatchInput,
    classification: MatchClassification,
    verdict: HarnessVerdict,
) -> BetLeg | None:
    if verdict.adjustment == "skip":
        return None

    selection = _main_selection(match, classification.scenario)
    if selection is None:
        return None

    level = pick_level(classification.confidence, verdict.adjustment)
    pct, amount = allocate(level, match.bankroll, verdict.adjustment)
    if amount == 0:
        return None

    market = _market_kind_for_scenario(classification.scenario)
    return BetLeg(
        market=market,
        selection=selection,
        level=level,
        stake_pct=pct,
        stake_amount=amount,
    )


def _main_selection(match: MatchInput, scenario: ScenarioType) -> str | None:
    """Pick the headline market + selection for the scenario.

    Conservative defaults — the recommender intentionally does NOT bet the
    juicy line (the user's spec calls -1.5 the comfortable hit, not -2). We
    bet the protective AH for spread scenarios and the totals for goal-market
    / pressure_under.
    """
    fav_team = match.home if match.market.favorite == "home" else match.away

    if scenario == "two_goal_landing":
        # Protective line one step inside the actual handicap.
        return f"{fav_team} -1.5"
    if scenario == "must_win_pressure":
        return f"{fav_team} -1"
    if scenario == "narrow_win":
        return f"{fav_team} -0.5"
    if scenario == "goal_market":
        return f"Over {match.totals.line}"
    if scenario == "pressure_under":
        return f"Under {match.totals.line}"
    return None


def _market_kind_for_scenario(scenario: ScenarioType):
    if scenario in ("goal_market", "pressure_under"):
        return "totals"
    return "AH"


def _build_secondary(
    match: MatchInput,
    classification: MatchClassification,
    verdict: HarnessVerdict,
) -> list[BetLeg]:
    """One secondary leg when the scenario has an obvious side market.

    Example: two_goal_landing → take Over <totals> at half the level of main.
    """
    if verdict.adjustment == "skip":
        return []
    scenario = classification.scenario
    legs: list[BetLeg] = []

    if scenario == "two_goal_landing":
        pct, amount = allocate("C", match.bankroll, verdict.adjustment)
        if amount > 0:
            legs.append(
                BetLeg(
                    market="totals",
                    selection=f"Over {match.totals.line}",
                    level="C",
                    stake_pct=pct,
                    stake_amount=amount,
                )
            )
    if scenario == "pressure_under":
        # BTTS=No often pairs cleanly with under in must-win-both spots.
        pct, amount = allocate("C", match.bankroll, verdict.adjustment)
        if amount > 0:
            legs.append(
                BetLeg(
                    market="BTTS",
                    selection="No",
                    level="C",
                    stake_pct=pct,
                    stake_amount=amount,
                )
            )

    return legs


def _build_correct_score(
    match: MatchInput,
    corridor: ScoreCorridor,
    verdict: HarnessVerdict,
) -> list[BetLeg]:
    """Small sprinkles on top 2-3 corridor scores; total ≤ 4% of bankroll."""
    if verdict.adjustment == "skip":
        return []

    candidates = corridor.scores[:3]
    if not candidates:
        return []

    per_leg_budget = (_CORRECT_SCORE_PCT_BUDGET / Decimal(len(candidates))).quantize(
        Decimal("0.001")
    )
    legs: list[BetLeg] = []
    for h, a in candidates:
        amount = (match.bankroll * per_leg_budget).quantize(Decimal("1"))
        if amount <= 0:
            continue
        legs.append(
            BetLeg(
                market="correct_score",
                selection=f"{h}-{a}",
                level=_CORRECT_SCORE_LEVEL,
                stake_pct=per_leg_budget,
                stake_amount=amount,
            )
        )
    return legs


def _build_avoid(match: MatchInput, classification: MatchClassification) -> list[str]:
    fav_team = match.home if match.market.favorite == "home" else match.away
    avoid: list[str] = []
    scenario = classification.scenario

    if scenario == "two_goal_landing":
        avoid.append(f"{fav_team} -2.5 (no protection if 2-0 / 2-1)")
    if scenario == "pressure_under":
        avoid.append(f"Over {match.totals.line} (both teams need a tie tactically)")
    if scenario == "rotation_trap":
        avoid.append(f"{fav_team} -1 or deeper")
    if scenario == "goal_market":
        avoid.append(f"{fav_team} -1.5 (line is shallow for a reason)")
    return avoid


def _reserve_live_copy(scenario: ScenarioType) -> str | None:
    if scenario == "rotation_trap":
        return "Hold full powder; revisit at HT if underdog leads."
    if scenario == "pressure_under":
        return "Reserve for live Under if 0-0 at HT — line will drift."
    if scenario == "two_goal_landing":
        return "Reserve 1 unit for live Over if 1-0 by 35'."
    return None


def _build_reserve_live(classification: MatchClassification) -> list[str]:
    note = _reserve_live_copy(classification.scenario)
    return [note] if note else []


# Re-exports so callers can compose without re-importing siblings.
__all__ = ["recommend"]
