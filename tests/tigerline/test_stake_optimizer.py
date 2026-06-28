"""V3.0 Sprint 3 — Stake Optimizer tests."""

from __future__ import annotations

from decimal import Decimal

from tigerline.stake_optimizer import (
    ExposureCaps,
    Pick,
    optimize_stakes,
)


def _pick(pid: str, **overrides) -> Pick:
    base = {
        "pick_id": pid,
        "match_id": "m1",
        "scenario": "two_goal_landing",
        "market": "AH",
        "selection": "Belgium -1.5",
        "level": "A",
        "adjustment": "normal",
        "favorite_team": "Belgium",
    }
    base.update(overrides)
    return Pick(**base)  # type: ignore[arg-type]


def test_single_pick_passes_through():
    bankroll = Decimal("5000")
    result = optimize_stakes(bankroll, [_pick("p1")])
    assert len(result.picks) == 1
    assert result.picks[0].cut_reason is None
    assert result.picks[0].final_amount > Decimal("0")


def test_per_scenario_cap_clamps():
    bankroll = Decimal("10000")
    # Four A-level picks in the same scenario, each requests 15% → would be 60%, scenario cap 30%.
    picks = [_pick(f"p{i}", match_id=f"m{i}", favorite_team=f"team{i}") for i in range(4)]
    caps = ExposureCaps(per_scenario=Decimal("0.30"), daily_total=Decimal("1.0"),
                       per_correlation_group=Decimal("1.0"))
    result = optimize_stakes(bankroll, picks, caps=caps)
    total = sum(p.final_pct for p in result.picks)
    assert total <= Decimal("0.30") + Decimal("0.01")
    assert any(p.cut_reason == "scenario_cap" for p in result.picks)


def test_correlation_cap_clamps_same_team():
    bankroll = Decimal("10000")
    # Two picks on Belgium (same team) in the same scenario.
    picks = [_pick("p1", match_id="m1"), _pick("p2", match_id="m1")]
    caps = ExposureCaps(per_correlation_group=Decimal("0.15"), daily_total=Decimal("1.0"),
                       per_scenario=Decimal("1.0"))
    result = optimize_stakes(bankroll, picks, caps=caps)
    belgium_total = sum(p.final_pct for p in result.picks if p.pick.favorite_team == "Belgium")
    assert belgium_total <= Decimal("0.15") + Decimal("0.01")


def test_daily_cap_is_hard_limit():
    bankroll = Decimal("10000")
    # Many picks in different scenarios so scenario cap doesn't bite first.
    scenarios = ["two_goal_landing", "must_win_pressure", "narrow_win", "goal_market"]
    picks = [
        _pick(f"p{i}", match_id=f"m{i}", scenario=scenarios[i], favorite_team=f"t{i}", market="AH")
        for i in range(4)
    ]
    caps = ExposureCaps(daily_total=Decimal("0.40"), per_scenario=Decimal("1.0"),
                       per_correlation_group=Decimal("1.0"))
    result = optimize_stakes(bankroll, picks, caps=caps)
    assert result.total_pct <= Decimal("0.40") + Decimal("0.01")
    assert any(p.cut_reason == "daily_cap" for p in result.picks)


def test_half_kelly_halves_requested_stake():
    bankroll = Decimal("10000")
    pick = _pick("p1", level="A+")
    full = optimize_stakes(bankroll, [pick])
    half = optimize_stakes(bankroll, [pick], enable_half_kelly=True)
    assert half.picks[0].final_amount * Decimal("2") == full.picks[0].final_amount


def test_skip_adjustment_yields_zero_amount():
    bankroll = Decimal("10000")
    pick = _pick("p1", adjustment="skip")
    result = optimize_stakes(bankroll, [pick])
    assert result.picks[0].final_amount == Decimal("0")


def test_empty_picks_returns_empty_allocation():
    result = optimize_stakes(Decimal("5000"), [])
    assert result.picks == []
    assert result.total_pct == Decimal("0")
    assert result.total_amount == Decimal("0")


def test_total_pct_and_amount_consistent():
    bankroll = Decimal("10000")
    picks = [_pick(f"p{i}", match_id=f"m{i}", favorite_team=f"t{i}",
                   scenario=["narrow_win", "must_win_pressure"][i % 2]) for i in range(3)]
    result = optimize_stakes(bankroll, picks)
    summed_pct = sum(p.final_pct for p in result.picks)
    summed_amount = sum(p.final_amount for p in result.picks)
    assert result.total_pct == summed_pct
    assert result.total_amount == summed_amount
