"""Review engine — settlement + scoring + suggestions."""

from __future__ import annotations

from tigerline.recommender import recommend
from tigerline.review import review


def test_belgium_nz_5_1_belgium_main_wins(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    r = review(m, plan, 5, 1)
    assert r.scenario_correct is True
    assert r.corridor_hit is False, "5-1 not in corridor — should surface widening hint"
    assert r.main_result == "win"  # Belgium -1.5 with +4 margin = win
    assert r.score >= 70
    assert any("widening" in s.lower() or "blowouts" in s.lower() for s in r.suggestions)


def test_belgium_nz_corridor_hit_for_2_0(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    r = review(m, plan, 2, 0)
    assert r.corridor_hit is True
    assert r.main_result == "win"  # 2-0 margin, -1.5 line → +0.5 → win
    assert r.score >= 90


def test_egypt_iran_1_1_corridor_holds(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    plan = recommend(m)
    r = review(m, plan, 1, 1)
    assert r.scenario_correct is True
    assert r.corridor_hit is True
    # Under 2.0 vs total=2 is a push (stake refund) — surfaced separately below.


def test_egypt_iran_push_when_total_equals_line(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    plan = recommend(m)
    r = review(m, plan, 1, 1)
    # Under 2.0 vs total=2 → push, not win.
    assert r.main_result == "push"


def test_egypt_iran_under_2_when_score_0_0_wins(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    plan = recommend(m)
    r = review(m, plan, 0, 0)
    assert r.corridor_hit is True
    assert r.main_result == "win"


def test_rotation_trap_skip_when_match_played_normally(load_fixture):
    m = load_fixture("rotation_trap_skip")
    plan = recommend(m)
    # Rotation trap → no main bet was placed.
    r = review(m, plan, 1, 0)
    assert plan.main_bet is None
    assert r.main_result == "skipped"
    assert r.scenario_correct is True


def test_score_bounded_0_to_100(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    plan = recommend(m)
    for hg, ag in [(0, 0), (5, 0), (1, 1), (9, 9)]:
        r = review(m, plan, hg, ag)
        assert 0 <= r.score <= 100
