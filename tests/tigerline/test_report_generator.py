"""V3.0 Sprint 5 — Report Generator tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tigerline.calibration_engine import BucketStat, CalibrationReport
from tigerline.classifier import classify
from tigerline.compliance_guard import DEFAULT_DISCLAIMER_ZH
from tigerline.consensus_engine import ConsensusReport
from tigerline.line_movement_analyzer import MovementReport
from tigerline.precision_score_engine import PrecisionScore
from tigerline.recommender import recommend
from tigerline.report_generator import (
    ReportContext,
    avoid_list_report,
    daily_pre_match_report,
    market_alert_report,
    post_match_review_report,
    weekly_calibration_report,
)
from tigerline.review import review


def _ctx(load_fixture) -> ReportContext:
    m = load_fixture("belgium_nz_two_goal")
    c = classify(m)
    p = recommend(m)
    precision = PrecisionScore(
        score=88,
        level="A",
        channel_scores={"tiger_clarity": 27, "ah_totals_coherence": 15},
        penalties_applied={},
        notes=["Movement: line -1.5 → -2 (strengthening)."],
    )
    movement = MovementReport(
        match_id=m.match_id,
        market_type="AH_full",
        bookmaker="pinnacle",
        opening_line=Decimal("-1.5"),
        current_line=Decimal("-2"),
        opening_price=Decimal("1.95"),
        current_price=Decimal("1.85"),
        line_delta=Decimal("-0.5"),
        price_delta=Decimal("-0.1"),
        velocity="moderate",
        direction="favorite_strengthening",
        crosses_key_handicaps=[Decimal("-1.75")],
        snapshots_used=2,
    )
    consensus = ConsensusReport(
        match_id=m.match_id,
        market_type="AH_full",
        n_books=3,
        consensus_line=Decimal("-1.75"),
        user_platform_line=Decimal("-1.5"),
        user_platform_edge="better_than_market",
        edge_magnitude=Decimal("0.25"),
        agreement="high",
        outlier_books=[],
    )
    return ReportContext(
        match=m, classification=c, plan=p,
        precision=precision, movement=movement, consensus=consensus,
    )


def test_daily_pre_match_pro_tier_contains_main_bet(load_fixture):
    ctx = _ctx(load_fixture)
    out = daily_pre_match_report(ctx, tier="pro", now=datetime(2026, 6, 22, 18, tzinfo=UTC))
    assert "Pre-Match Brief" in out
    assert "Main" in out
    assert "Precision score" in out
    assert DEFAULT_DISCLAIMER_ZH in out


def test_daily_pre_match_free_tier_hides_money(load_fixture):
    ctx = _ctx(load_fixture)
    out = daily_pre_match_report(ctx, tier="free", now=datetime(2026, 6, 22, 18, tzinfo=UTC))
    assert "Scenario" in out
    assert "Precision score" not in out
    assert "Main" not in out
    assert DEFAULT_DISCLAIMER_ZH in out


def test_daily_pre_match_vip_includes_movement_and_consensus(load_fixture):
    ctx = _ctx(load_fixture)
    out = daily_pre_match_report(ctx, tier="vip", now=datetime(2026, 6, 22, 18, tzinfo=UTC))
    assert "Market movement" in out
    assert "Consensus" in out


def test_market_alert_report_contains_movement(load_fixture):
    ctx = _ctx(load_fixture)
    out = market_alert_report(ctx.match, ctx.movement, tier="pro",  # type: ignore[arg-type]
                              now=datetime(2026, 6, 22, 18, tzinfo=UTC))
    assert "Market Alert" in out
    assert "favorite_strengthening" in out
    assert DEFAULT_DISCLAIMER_ZH in out


def test_post_match_review_report(load_fixture):
    ctx = _ctx(load_fixture)
    r = review(ctx.match, ctx.plan, 5, 1)
    out = post_match_review_report(ctx.match, ctx.plan, r, tier="pro",
                                   now=datetime(2026, 6, 22, 22, tzinfo=UTC))
    assert "Post-Match Review" in out
    assert "5-1" in out
    assert "Main result" in out


def test_weekly_calibration_report_with_buckets():
    cal = CalibrationReport(
        n_reviewed=10,
        brier=0.15,
        log_loss=0.42,
        expected_calibration_error=0.05,
        buckets=[
            BucketStat(bucket_lo=90, bucket_hi=101, n=4, avg_predicted=0.9,
                       observed_hit_rate=0.75, main_win_rate=0.75),
        ],
    )
    out = weekly_calibration_report(cal, now=datetime(2026, 6, 22, tzinfo=UTC))
    assert "Brier score" in out
    assert "0.150" in out
    assert "90-100" in out


def test_avoid_list_report(load_fixture):
    ctx = _ctx(load_fixture)
    out = avoid_list_report([ctx.plan], tier="basic",
                            now=datetime(2026, 6, 22, tzinfo=UTC))
    assert "Avoid List" in out
    assert ctx.plan.match_id in out


def test_avoid_list_empty_when_no_plans():
    out = avoid_list_report([], tier="basic", now=datetime(2026, 6, 22, tzinfo=UTC))
    assert "no avoid items" in out
