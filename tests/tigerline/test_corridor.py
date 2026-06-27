"""Corridor builder — coverage per scenario + away-favorite mirror flip."""

from __future__ import annotations

from tigerline.corridor import build_corridor
from tigerline.models import MatchInput


def _flip_favorite(m: MatchInput) -> MatchInput:
    data = m.model_dump(mode="json")
    data["market"]["favorite"] = "away"
    data["market"]["line"] = str(-m.market.line)
    return MatchInput.model_validate(data)


def test_two_goal_landing_corridor_has_known_scores(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    c = build_corridor("two_goal_landing", m)
    assert c.primary == (2, 0)
    assert (2, 0) in c.scores
    assert (3, 1) in c.scores
    assert 3 <= len(c.scores) <= 8


def test_pressure_under_corridor(load_fixture):
    m = load_fixture("egypt_iran_pressure_under")
    c = build_corridor("pressure_under", m)
    assert c.primary == (1, 1)
    assert (0, 0) in c.scores
    assert (1, 1) in c.scores


def test_rotation_trap_corridor_is_underdog_friendly(load_fixture):
    m = load_fixture("rotation_trap_skip")
    c = build_corridor("rotation_trap", m)
    assert (1, 1) in c.scores
    assert any(s[0] <= s[1] for s in c.scores), "trap corridor must allow upset"


def test_corridor_flips_for_away_favorite(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    flipped = _flip_favorite(m)
    c = build_corridor("two_goal_landing", flipped)
    # Template has (2,0) primary as home-favorite; flipped should be (0,2).
    assert c.primary == (0, 2)
    assert (0, 2) in c.scores


def test_primary_is_always_in_scores(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    for scenario in [
        "two_goal_landing",
        "must_win_pressure",
        "pressure_under",
        "goal_market",
        "narrow_win",
        "rotation_trap",
        "unclear_skip",
    ]:
        c = build_corridor(scenario, m)  # type: ignore[arg-type]
        assert c.primary in c.scores, f"primary not in scores for {scenario}"


def test_unclear_skip_corridor_is_minimal(load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    c = build_corridor("unclear_skip", m)
    assert len(c.scores) == 1
    assert c.primary == (1, 1)
