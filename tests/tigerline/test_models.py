"""Models + parser round-trip + Decimal strictness."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from tigerline.models import (
    AsianHandicap,
    BetLeg,
    BetPlan,
    MatchInput,
    Totals,
)
from tigerline.parser import parse_match


def test_load_belgium_nz_fixture(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    assert m.match_id == "2026-wc-belgium-newzealand"
    assert m.market.line == Decimal("-1.75")
    assert m.totals.line == Decimal("3.0")
    assert m.team_need.home == "neutral"
    assert m.team_need.away == "rotation"
    assert m.bankroll == Decimal("5000")


def test_load_egypt_iran_fixture(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    assert m.market.line == Decimal("-0.25")
    assert m.totals.line == Decimal("2.0")
    assert m.team_need.home == "must_win" and m.team_need.away == "must_win"
    assert m.group_context.final_round is True


def test_load_rotation_trap_fixture(load_fixture):
    m = load_fixture("rotation_trap_skip")
    assert m.team_need.home == "rotation"
    assert "favorite_already_qualified" in m.risk_flags


def test_match_input_is_frozen(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    with pytest.raises(ValidationError):
        m.match_id = "mutated"  # type: ignore[misc]  # frozen — must raise


def test_float_money_rejected_on_parser():
    """Float-literal handicap lines must be rejected (binary rounding)."""
    payload = {
        "match_id": "test-id",
        "kickoff_utc": "2026-06-22T22:00:00+00:00",
        "home": "A",
        "away": "B",
        "group_context": {"standings_summary": "x"},
        "team_need": {"home": "neutral", "away": "neutral"},
        "market": {
            "line": -1.75,  # ← bare float
            "favorite": "home",
            "home_price": "1.9",
            "away_price": "1.9",
        },
        "totals": {"line": "3.0", "over_price": "1.9", "under_price": "1.9"},
        "bankroll": "5000",
    }
    with pytest.raises(ValidationError) as ei:
        parse_match(payload)
    assert "float literals are not accepted" in str(ei.value)


def test_string_decimal_accepted():
    ah = AsianHandicap(line="-1.75", favorite="home", home_price="1.92", away_price="1.90")
    assert ah.line == Decimal("-1.75")


def test_totals_line_must_be_positive():
    with pytest.raises(ValidationError):
        Totals(line="0", over_price="1.9", under_price="1.9")


def test_bet_plan_round_trip():
    leg = BetLeg(
        market="AH",
        selection="Belgium -1.5",
        level="A+",
        stake_pct="0.20",
        stake_amount="1000",
    )
    plan = BetPlan(match_id="x", scenario="two_goal_landing", main_bet=leg)
    dumped = plan.model_dump_json()
    restored = BetPlan.model_validate_json(dumped)
    assert restored.main_bet == leg
    assert restored.scenario == "two_goal_landing"


def test_match_id_pattern_rejects_uppercase():
    with pytest.raises(ValidationError):
        MatchInput.model_validate(
            {
                "match_id": "Bad-ID",
                "kickoff_utc": "2026-06-22T22:00:00+00:00",
                "home": "A",
                "away": "B",
                "group_context": {"standings_summary": "x"},
                "team_need": {"home": "neutral", "away": "neutral"},
                "market": {
                    "line": "-1",
                    "favorite": "home",
                    "home_price": "1.9",
                    "away_price": "1.9",
                },
                "totals": {"line": "3.0", "over_price": "1.9", "under_price": "1.9"},
                "bankroll": "5000",
            }
        )
