"""Post-match review engine.

Takes the original ``MatchInput``, the engine's ``BetPlan`` from the analysis
run, and the actual ``(home_goals, away_goals)`` and produces a ``ReviewResult``:
- did the scenario hold up?
- did the corridor catch the score?
- did the main bet win / push / lose / half / void?
- 0-100 score (40 scenario + 30 corridor + 30 main_result)
- bulleted suggestions for rule tweaks

The suggestions feed ``rule_updates`` table — they accumulate over a season and
become the raw material for tuning the YAML.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from tigerline.classifier import classify
from tigerline.corridor import build_corridor
from tigerline.models import (
    BetLeg,
    BetPlan,
    MainResult,
    MatchInput,
    ReviewResult,
    ScenarioType,
)

_SCENARIO_WEIGHT = 40
_CORRIDOR_WEIGHT = 30
_MAIN_WEIGHT = 30


def review(
    match: MatchInput,
    plan: BetPlan,
    home_goals: int,
    away_goals: int,
) -> ReviewResult:
    """Score the engine's call against reality."""
    actual = (home_goals, away_goals)

    # Re-classify on demand — the YAML may have been edited since the analysis,
    # which is exactly the kind of drift we want the review to surface.
    fresh = classify(match)
    scenario_correct = fresh.scenario == plan.scenario

    corridor = build_corridor(plan.scenario, match)
    corridor_hit = actual in corridor.scores

    main_result = _score_main_bet(match, plan.main_bet, home_goals, away_goals, plan.scenario)

    score = _scorecard(scenario_correct, corridor_hit, main_result)
    suggestions = _suggest(plan.scenario, scenario_correct, corridor_hit, main_result, actual)

    return ReviewResult(
        match_id=match.match_id,
        actual_score=actual,
        scenario_correct=scenario_correct,
        corridor_hit=corridor_hit,
        main_result=main_result,
        score=score,
        suggestions=suggestions,
    )


def _score_main_bet(
    match: MatchInput,
    leg: BetLeg | None,
    home_goals: int,
    away_goals: int,
    scenario: ScenarioType,
) -> MainResult:
    if leg is None:
        return "skipped"

    if leg.market == "AH":
        return _settle_ah(match, leg, home_goals, away_goals)
    if leg.market == "totals":
        return _settle_totals(match, leg, home_goals + away_goals)
    if leg.market == "correct_score":
        return _settle_correct_score(leg, home_goals, away_goals)
    if leg.market == "BTTS":
        return _settle_btts(leg, home_goals, away_goals)
    return "void"


def _settle_ah(match: MatchInput, leg: BetLeg, hg: int, ag: int) -> MainResult:
    """Settle an Asian-handicap leg given the actual score.

    The recommender always backs the favorite at a protective line (e.g. -1.5
    for two_goal_landing). We parse the selection string to find the line and
    side.
    """
    line, side = _parse_ah_selection(leg.selection, match)
    if line is None:
        return "void"
    margin = (hg - ag) if side == "home" else (ag - hg)
    # Backed favorite with handicap `line` (negative). Result = margin + line.
    settled = Decimal(margin) + line
    return _classify_ah_outcome(settled)


def _classify_ah_outcome(settled: Decimal) -> MainResult:
    if settled > Decimal("0.25"):
        return "win"
    if settled == Decimal("0.25"):
        return "half_win"
    if settled == Decimal("0"):
        return "push"
    if settled == Decimal("-0.25"):
        return "half_lose"
    return "lose"


def _parse_ah_selection(selection: str, match: MatchInput) -> tuple[Decimal | None, str]:
    # Format the recommender produces: "<Team> -1.5", "<Team> -1", etc.
    parts = selection.rsplit(" ", 1)
    if len(parts) != 2:
        return None, "home"
    team, line_str = parts
    try:
        line = Decimal(line_str)
    except (InvalidOperation, ValueError):
        return None, "home"
    side = "home" if team == match.home else "away"
    return line, side


def _settle_totals(match: MatchInput, leg: BetLeg, total: int) -> MainResult:
    line = match.totals.line
    selection_lower = leg.selection.lower()
    if selection_lower.startswith("over"):
        side = "over"
    elif selection_lower.startswith("under"):
        side = "under"
    else:
        return "void"
    diff = Decimal(total) - line if side == "over" else line - Decimal(total)
    return _classify_ah_outcome(diff)


def _settle_correct_score(leg: BetLeg, hg: int, ag: int) -> MainResult:
    try:
        h_str, a_str = leg.selection.split("-", 1)
        h = int(h_str)
        a = int(a_str)
    except (ValueError, IndexError):
        return "void"
    return "win" if (h, a) == (hg, ag) else "lose"


def _settle_btts(leg: BetLeg, hg: int, ag: int) -> MainResult:
    both_scored = hg > 0 and ag > 0
    sel = leg.selection.lower()
    if sel == "yes":
        return "win" if both_scored else "lose"
    if sel == "no":
        return "lose" if both_scored else "win"
    return "void"


def _scorecard(scenario_ok: bool, corridor_ok: bool, main: MainResult) -> int:
    score = 0
    if scenario_ok:
        score += _SCENARIO_WEIGHT
    if corridor_ok:
        score += _CORRIDOR_WEIGHT
    score += _main_points(main)
    return min(100, max(0, score))


def _main_points(main: MainResult) -> int:
    return {
        "win": _MAIN_WEIGHT,
        "half_win": _MAIN_WEIGHT * 2 // 3,
        "push": _MAIN_WEIGHT // 2,
        "half_lose": _MAIN_WEIGHT // 3,
        "lose": 0,
        "void": _MAIN_WEIGHT // 2,
        "skipped": _MAIN_WEIGHT // 2,  # skipping correctly = neutral
    }[main]


def _suggest(
    scenario: ScenarioType,
    scenario_ok: bool,
    corridor_ok: bool,
    main: MainResult,
    actual: tuple[int, int],
) -> list[str]:
    out: list[str] = []
    if not scenario_ok:
        out.append(
            f"Scenario mismatch — re-classified differently on review. "
            f"Inspect classification_rules.yaml ordering for {scenario}."
        )
    if scenario_ok and not corridor_ok:
        out.append(
            f"Scenario {scenario} held but corridor missed {actual[0]}-{actual[1]}. "
            "Consider widening corridor_templates.yaml for blowouts / edge scores."
        )
    if main == "lose" and scenario_ok and corridor_ok:
        out.append(
            "Engine read the match correctly but main bet lost — "
            "review main-bet line choice (too aggressive vs. corridor signal?)."
        )
    if main == "skipped" and corridor_ok:
        out.append("Skip stood — corridor caught the score; harness held the line.")
    return out


__all__ = ["review"]
