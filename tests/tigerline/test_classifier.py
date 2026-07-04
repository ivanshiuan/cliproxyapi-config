"""Classifier — one canonical fixture per scenario + unclear_skip fallback."""

from __future__ import annotations

from decimal import Decimal

from tigerline.classifier import classify
from tigerline.models import MatchInput


def _swap(m: MatchInput, **overrides) -> MatchInput:
    """Build a MatchInput by swapping fields on an existing fixture's dict."""
    data = m.model_dump(mode="json")
    for key, value in overrides.items():
        cur = data
        parts = key.split(".")
        for part in parts[:-1]:
            cur = cur[part]
        cur[parts[-1]] = value
    return MatchInput.model_validate(data)


def test_belgium_nz_classifies_two_goal_landing(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    out = classify(m)
    assert out.scenario == "two_goal_landing"
    assert out.confidence >= Decimal("0.6")
    assert out.reasons


def test_egypt_iran_classifies_pressure_under(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    out = classify(m)
    assert out.scenario == "pressure_under"
    assert out.confidence >= Decimal("0.4")


def test_rotation_short_circuits_even_with_deep_handicap(load_fixture):
    """rotation_trap must beat two_goal_landing when both could match."""
    m = load_fixture("rotation_trap_skip")
    out = classify(m)
    assert out.scenario == "rotation_trap"


def test_unclear_skip_when_no_rule_fires(load_fixture):
    # Tweak Belgium NZ so neither two_goal_landing nor goal_market apply:
    # handicap inside (1.0, 1.5) gap and totals below 2.75 (no rule covers it).
    m = load_fixture("belgium_nz_two_goal")
    weird = _swap(
        m,
        **{
            "market.line": "-1.25",
            "totals.line": "2.4",
            "team_need.home": "neutral",
            "team_need.away": "neutral",
            "group_context.final_round": False,
        },
    )
    out = classify(weird)
    assert out.scenario == "unclear_skip"
    assert out.confidence == Decimal("0")


def test_must_win_pressure_scenario(load_fixture):
    """Strong favorite with must_win + moderate totals."""
    m = load_fixture("belgium_nz_two_goal")
    must_win = _swap(
        m,
        **{
            "market.line": "-1.0",
            "totals.line": "2.5",
            "team_need.home": "must_win",
            "team_need.away": "neutral",
        },
    )
    out = classify(must_win)
    assert out.scenario == "must_win_pressure"


def test_goal_market_scenario(load_fixture):
    """Shallow handicap + open totals → goal_market."""
    m = load_fixture("belgium_nz_two_goal")
    goals = _swap(
        m,
        **{
            "market.line": "-0.5",
            "totals.line": "3.0",
            "team_need.home": "neutral",
            "team_need.away": "neutral",
            "group_context.final_round": False,
        },
    )
    out = classify(goals)
    assert out.scenario == "goal_market"


def test_narrow_win_scenario(load_fixture):
    """Light favorite with low scoring expectation."""
    m = load_fixture("belgium_nz_two_goal")
    narrow = _swap(
        m,
        **{
            "market.line": "-0.5",
            "totals.line": "2.25",
            "team_need.home": "neutral",
            "team_need.away": "neutral",
            "group_context.final_round": False,
        },
    )
    out = classify(narrow)
    assert out.scenario == "narrow_win"


def test_confidence_falls_near_boundary(load_fixture):
    """A line exactly at the gte threshold should drop to LOW confidence."""
    m = load_fixture("belgium_nz_two_goal")
    edge = _swap(m, **{"market.line": "-1.5", "totals.line": "2.75"})
    out = classify(edge)
    assert out.scenario == "two_goal_landing"
    assert out.confidence == Decimal("0.4")
