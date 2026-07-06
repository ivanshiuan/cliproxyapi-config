"""7-scenario classifier.

Reads ``config/classification_rules.yaml`` (cached) and walks rules in order —
the first whose ``when`` clause is fully satisfied wins. No match → ``unclear_skip``.
"""

from __future__ import annotations

from decimal import Decimal

from tigerline.models import MatchClassification, MatchInput, ScenarioType, TeamNeedEnum
from tigerline.rules import ClassificationRule, WhenClause, load_classification_rules

_HIGH = Decimal("0.9")
_LOW = Decimal("0.4")


def classify(
    match: MatchInput,
    rules: tuple[ClassificationRule, ...] | None = None,
) -> MatchClassification:
    """Classify a match into one of 7 scenarios."""
    if rules is None:
        rules = load_classification_rules()

    h_abs = abs(match.market.line)
    t_line = match.totals.line
    fav_need, und_need = _favorite_and_underdog_need(match)

    for rule in rules:
        if _rule_matches(rule.when, h_abs, t_line, fav_need, und_need, match):
            return MatchClassification(
                scenario=rule.scenario,
                confidence=_confidence(rule.when, h_abs, t_line),
                reasons=[rule.reason],
            )

    return MatchClassification(
        scenario="unclear_skip",
        confidence=Decimal("0"),
        reasons=["No classification rule matched — skip and revisit input."],
    )


def _favorite_and_underdog_need(match: MatchInput) -> tuple[TeamNeedEnum, TeamNeedEnum]:
    if match.market.favorite == "home":
        return match.team_need.home, match.team_need.away
    return match.team_need.away, match.team_need.home


def _rule_matches(
    w: WhenClause,
    h_abs: Decimal,
    t_line: Decimal,
    fav_need: TeamNeedEnum,
    und_need: TeamNeedEnum,
    match: MatchInput,
) -> bool:
    if w.handicap_abs_gte is not None and not h_abs >= w.handicap_abs_gte:
        return False
    if w.handicap_abs_lte is not None and not h_abs <= w.handicap_abs_lte:
        return False
    if w.totals_gte is not None and not t_line >= w.totals_gte:
        return False
    if w.totals_lte is not None and not t_line <= w.totals_lte:
        return False
    if w.favorite_need is not None and fav_need != w.favorite_need:
        return False
    if w.favorite_need_in is not None and fav_need not in w.favorite_need_in:
        return False
    if w.underdog_need is not None and und_need != w.underdog_need:
        return False
    if w.underdog_need_in is not None and und_need not in w.underdog_need_in:
        return False
    if w.either_need_in is not None and not (
        fav_need in w.either_need_in or und_need in w.either_need_in
    ):
        return False
    if w.final_round is not None and match.group_context.final_round != w.final_round:
        return False
    return not (
        w.same_time_kickoff is not None
        and match.group_context.same_time_kickoff != w.same_time_kickoff
    )


def _confidence(w: WhenClause, h_abs: Decimal, t_line: Decimal) -> Decimal:
    """Distance-from-boundary heuristic.

    Betting lines are quantized in 0.25 steps, so we measure margin in steps,
    not in relative percent. ≥0.50 step margin on the worst dimension → HIGH;
    ≥0.25 step → MID; on the line → LOW. If a rule sets only enum bounds,
    confidence is HIGH (no numeric edge case to surface).
    """
    margins: list[Decimal] = []
    pairs: list[tuple[Decimal | None, Decimal, str]] = [
        (w.handicap_abs_gte, h_abs, "gte"),
        (w.handicap_abs_lte, h_abs, "lte"),
        (w.totals_gte, t_line, "gte"),
        (w.totals_lte, t_line, "lte"),
    ]
    for bound, value, kind in pairs:
        if bound is None:
            continue
        if kind == "gte":
            margins.append(value - bound)
        else:
            margins.append(bound - value)

    if not margins:
        return _HIGH
    worst = min(margins)
    # Betting lines step in 0.25; one step past the threshold = comfortable hit.
    # Sitting exactly on the threshold is the close-call we want to surface.
    if worst >= Decimal("0.25"):
        return _HIGH
    return _LOW


__all__ = ["ScenarioType", "classify"]
