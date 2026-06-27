"""Canonical reproductions — Ivan's manually-validated cases as living tests.

Green here = engine handles the three archetype matches the user vouched for
in the V2.0 plan. If a rule edit breaks any of these, the YAML went sideways.
"""

from __future__ import annotations

from decimal import Decimal

from tigerline.classifier import classify
from tigerline.corridor import build_corridor
from tigerline.harness import evaluate as harness_eval
from tigerline.recommender import recommend
from tigerline.review import review


def test_canonical_belgium_5_1_newzealand(load_fixture):
    """Two-goal landing — Belgium 5:1 NZ.

    Per the V2.0 plan: scenario two_goal_landing, confidence ≥0.8, corridor
    contains (2,0) and (3,1) with primary=(2,0), main_bet=Belgium -1.5 at A/A+,
    pct 0.12-0.25. Review of 5-1: scenario_correct, corridor_hit=False (5-1 too
    big), main=win, score ≥70.
    """
    m = load_fixture("belgium_nz_two_goal")
    c = classify(m)
    assert c.scenario == "two_goal_landing"
    assert c.confidence >= Decimal("0.8")

    corridor = build_corridor(c.scenario, m)
    assert corridor.primary == (2, 0)
    assert (2, 0) in corridor.scores
    assert (3, 1) in corridor.scores

    plan = recommend(m)
    assert plan.main_bet is not None
    assert plan.main_bet.selection == "Belgium -1.5"
    assert plan.main_bet.level in ("A", "A+")
    assert Decimal("0.12") <= plan.main_bet.stake_pct <= Decimal("0.25")

    r = review(m, plan, 5, 1)
    assert r.scenario_correct is True
    assert r.corridor_hit is False
    assert r.main_result == "win"
    assert r.score >= 70
    assert any(
        "widening" in s.lower() or "blowout" in s.lower() for s in r.suggestions
    ), "engine must surface 'corridor too narrow' hint on a 5-1 blowout"


def test_canonical_egypt_1_1_iran(load_fixture):
    """Pressure-under — Egypt 1:1 Iran (must-win-both, low totals).

    Corridor contains (1,1) and (0,0). main_bet starts with 'Under'.
    Review of 1-1: corridor_hit=True. (Total=2 vs line=2 is a push on the
    main bet — separately verified in test_review.py; the canonical test
    here cares about scenario + corridor consistency, not main settlement.)
    """
    m = load_fixture("egypt_iran_pressure_under")
    c = classify(m)
    assert c.scenario == "pressure_under"

    corridor = build_corridor(c.scenario, m)
    assert (1, 1) in corridor.scores
    assert (0, 0) in corridor.scores

    plan = recommend(m)
    assert plan.main_bet is not None
    assert plan.main_bet.selection.startswith("Under")
    assert plan.main_bet.market == "totals"

    r = review(m, plan, 1, 1)
    assert r.scenario_correct is True
    assert r.corridor_hit is True
    assert r.main_result in ("win", "push", "half_win")


def test_canonical_rotation_trap_skip(load_fixture):
    """Rotation trap — favorite is rotating, market doesn't know.

    Engine must: scenario=rotation_trap, harness adjustment=skip,
    main_bet=None, avoid non-empty, correct_score=[].
    """
    m = load_fixture("rotation_trap_skip")
    c = classify(m)
    assert c.scenario == "rotation_trap"

    v = harness_eval(m, c)
    assert v.adjustment == "skip"

    plan = recommend(m)
    assert plan.main_bet is None
    assert plan.avoid, "trap must list what NOT to touch"
    assert plan.correct_score == []
    assert plan.reserve_live, "trap should advise reserving for live action"
