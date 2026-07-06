"""HARNESS-GATE verdict logic."""

from __future__ import annotations

from decimal import Decimal

from tigerline.classifier import classify
from tigerline.harness import evaluate
from tigerline.models import MatchClassification


def test_skip_when_scenario_is_rotation_trap(load_fixture):
    m = load_fixture("rotation_trap_skip")
    c = classify(m)
    v = evaluate(m, c)
    assert v.adjustment == "skip"
    assert v.notes


def test_skip_when_favorite_already_qualified_flag(load_fixture):
    m = load_fixture("rotation_trap_skip")  # already has the flag
    c = classify(m)
    v = evaluate(m, c)
    assert v.adjustment == "skip"


def test_upgrade_on_clean_two_goal_landing(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    high_conf = MatchClassification(
        scenario="two_goal_landing", confidence=Decimal("0.9"), reasons=["x"]
    )
    v = evaluate(m, high_conf)
    assert v.adjustment == "upgrade"


def test_downgrade_when_underdog_must_win_against_two_goal_landing(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    # Force underdog must_win
    data = m.model_dump(mode="json")
    data["team_need"]["away"] = "must_win"
    from tigerline.models import MatchInput  # local import to avoid top-level pollution

    m2 = MatchInput.model_validate(data)
    high = MatchClassification(
        scenario="two_goal_landing", confidence=Decimal("0.9"), reasons=["x"]
    )
    v = evaluate(m2, high)
    assert v.adjustment == "downgrade"


def test_downgrade_on_low_confidence(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    low = MatchClassification(
        scenario="two_goal_landing", confidence=Decimal("0.4"), reasons=["x"]
    )
    v = evaluate(m, low)
    assert v.adjustment == "downgrade"


def test_normal_for_pressure_under(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    c = classify(m)
    v = evaluate(m, c)
    # Pressure under hits the same_time_kickoff branch — but the special-case
    # excludes pressure_under from the downgrade trigger.
    assert v.adjustment in ("normal", "upgrade")
