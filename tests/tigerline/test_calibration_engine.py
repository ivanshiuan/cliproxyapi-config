"""V3.0 Sprint 4 — Calibration Engine tests.

These tests build the necessary upstream rows in the test DB (matches +
classifications + recommendations + results) and then exercise the
calibration aggregator.
"""

from __future__ import annotations

from decimal import Decimal

from tigerline.calibration_engine import calibrate
from tigerline.classifier import classify
from tigerline.recommender import recommend
from tigerline.review import review
from tigerline.storage import (
    save_classification,
    save_match,
    save_recommendation,
    save_review,
)


def _seed_and_review(db, fixture_loader, score: tuple[int, int]) -> None:
    """Run the full analyze → review pipeline against a fixture and persist it."""
    m = fixture_loader("belgium_nz_two_goal")
    save_match(db, m)
    c = classify(m)
    save_classification(db, m.match_id, c)
    plan = recommend(m)
    save_recommendation(db, plan, m.bankroll)
    r = review(m, plan, *score)
    save_review(db, r)


def test_calibrate_returns_empty_report_when_no_data(db):
    r = calibrate(db)
    assert r.n_reviewed == 0
    assert r.buckets == []


def test_calibrate_basic_brier_for_perfect_prediction(db, load_fixture, raw_fixture):
    """One match, confidence 0.9, corridor hits → low Brier."""
    _seed_and_review(db, load_fixture, (2, 0))
    r = calibrate(db)
    assert r.n_reviewed == 1
    # confidence 0.9 vs outcome 1.0 → (0.9-1)^2 = 0.01
    assert r.brier < 0.05


def test_calibrate_brier_when_corridor_misses(db, load_fixture):
    """Confidence 0.9 but corridor missed (5-1) → Brier should be higher."""
    _seed_and_review(db, load_fixture, (5, 1))
    r = calibrate(db)
    # confidence 0.9, outcome 0 → (0.9-0)^2 = 0.81
    assert r.brier > 0.5


def test_calibrate_buckets_predictions(db, load_fixture):
    _seed_and_review(db, load_fixture, (2, 0))
    r = calibrate(db)
    # confidence 0.9 → bucket [90, 101)
    assert any(b.bucket_lo == 90 for b in r.buckets)
    high_bucket = next(b for b in r.buckets if b.bucket_lo == 90)
    assert high_bucket.n == 1
    assert high_bucket.observed_hit_rate == 1.0


def test_calibrate_main_win_rate(db, load_fixture):
    _seed_and_review(db, load_fixture, (2, 0))  # main bet wins
    r = calibrate(db)
    bucket = next(b for b in r.buckets if b.bucket_lo == 90)
    assert bucket.main_win_rate == 1.0


def test_calibrate_log_loss_well_defined(db, load_fixture):
    _seed_and_review(db, load_fixture, (2, 0))
    r = calibrate(db)
    # log_loss should be finite + small for confident correct prediction
    assert r.log_loss < 0.5
    assert r.log_loss > 0


def test_ece_zero_when_predictions_match_outcomes(db, load_fixture):
    """One perfect-confidence hit gives ECE = |0.9 - 1.0| = 0.1."""
    _seed_and_review(db, load_fixture, (2, 0))
    r = calibrate(db)
    assert r.expected_calibration_error <= Decimal("0.15")
