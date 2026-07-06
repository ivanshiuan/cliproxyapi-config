"""Recommender — end-to-end pipeline for canonical fixtures."""

from __future__ import annotations

from decimal import Decimal

from tigerline.recommender import recommend


def test_belgium_nz_yields_main_bet(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    assert plan.scenario == "two_goal_landing"
    assert plan.main_bet is not None
    assert plan.main_bet.selection == "Belgium -1.5"
    assert plan.main_bet.level in ("A", "A+")
    assert plan.main_bet.stake_pct >= Decimal("0.12")
    assert plan.main_bet.stake_pct <= Decimal("0.25")
    assert plan.main_bet.market == "AH"


def test_belgium_nz_has_secondary_over(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    assert any(leg.market == "totals" and leg.selection.startswith("Over") for leg in plan.secondary)


def test_belgium_nz_correct_score_corridor(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    selections = {leg.selection for leg in plan.correct_score}
    # primary (2,0) → "2-0" must be a sprinkle
    assert "2-0" in selections


def test_belgium_nz_avoid_list_includes_deep_line(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    assert any("-2.5" in s for s in plan.avoid)


def test_egypt_iran_main_bet_is_under(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    plan = recommend(m)
    assert plan.scenario == "pressure_under"
    assert plan.main_bet is not None
    assert plan.main_bet.selection.startswith("Under")
    assert plan.main_bet.market == "totals"


def test_rotation_trap_skips_main_bet(load_fixture):
    m = load_fixture("rotation_trap_skip")
    plan = recommend(m)
    assert plan.scenario == "rotation_trap"
    assert plan.main_bet is None
    assert plan.secondary == []
    assert plan.correct_score == []
    assert plan.avoid


def test_rotation_trap_advises_live_powder(load_fixture):
    m = load_fixture("rotation_trap_skip")
    plan = recommend(m)
    assert plan.reserve_live, "trap scenario must surface live-action advice"
    assert any("powder" in s.lower() or "ht" in s.lower() for s in plan.reserve_live)
