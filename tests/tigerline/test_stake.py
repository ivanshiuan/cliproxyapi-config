"""Stake allocator — band edges + level picker."""

from __future__ import annotations

from decimal import Decimal

from tigerline.stake import allocate, pick_level


def test_a_plus_normal_uses_default():
    pct, amt = allocate("A+", Decimal("5000"), "normal")
    assert pct == Decimal("0.20")
    assert amt == Decimal("1000")


def test_a_plus_upgrade_clamps_to_max():
    pct, _ = allocate("A+", Decimal("5000"), "upgrade")
    assert pct == Decimal("0.25")


def test_a_plus_downgrade_clamps_to_min():
    pct, _ = allocate("A+", Decimal("5000"), "downgrade")
    assert pct == Decimal("0.18")


def test_skip_zeroes_regardless_of_level():
    pct, amt = allocate("A+", Decimal("5000"), "skip")
    assert pct == Decimal("0")
    assert amt == Decimal("0")


def test_level_d_is_zero():
    pct, amt = allocate("D", Decimal("5000"), "normal")
    assert (pct, amt) == (Decimal("0"), Decimal("0"))


def test_amount_rounds_to_whole_unit():
    pct, amt = allocate("B", Decimal("777"), "normal")
    # 777 * 0.09 = 69.93 → rounds to 70
    assert amt == Decimal("70")
    assert pct == Decimal("0.09")


def test_pick_level_high_confidence_yields_a():
    assert pick_level(Decimal("0.9"), "normal") == "A"
    assert pick_level(Decimal("0.9"), "upgrade") == "A+"


def test_pick_level_skip_is_d():
    assert pick_level(Decimal("0.9"), "skip") == "D"
    assert pick_level(Decimal("0.4"), "skip") == "D"


def test_pick_level_low_confidence_downgrades_to_d():
    assert pick_level(Decimal("0.2"), "downgrade") == "D"


def test_pick_level_mid_confidence(load_fixture):
    assert pick_level(Decimal("0.6"), "normal") == "B"
    assert pick_level(Decimal("0.6"), "upgrade") == "A"
    assert pick_level(Decimal("0.45"), "normal") == "C"
