"""V3.0 Sprint 3 — Precision Score Engine tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tigerline.consensus_engine import ConsensusReport
from tigerline.line_movement_analyzer import MovementReport
from tigerline.models import (
    HarnessVerdict,
    MatchClassification,
    OddsSnapshot,
    ScoreCorridor,
    SourceMetadata,
)
from tigerline.precision_score_engine import score_precision


def _classification(scenario: str = "two_goal_landing", conf: str = "0.9") -> MatchClassification:
    return MatchClassification(scenario=scenario, confidence=Decimal(conf), reasons=["x"])  # type: ignore[arg-type]


def _corridor(n_scores: int = 3) -> ScoreCorridor:
    scores = [(i, 0) for i in range(n_scores)]
    return ScoreCorridor(scores=scores, primary=scores[0])


def _harness(adjustment: str = "normal") -> HarnessVerdict:
    return HarnessVerdict(adjustment=adjustment, notes=[])  # type: ignore[arg-type]


def _movement(direction: str = "favorite_strengthening") -> MovementReport:
    return MovementReport(
        match_id="m1",
        market_type="AH_full",
        bookmaker="pinnacle",
        opening_line=Decimal("-1.5"),
        current_line=Decimal("-2"),
        opening_price=Decimal("1.95"),
        current_price=Decimal("1.85"),
        line_delta=Decimal("-0.5"),
        price_delta=Decimal("-0.1"),
        velocity="moderate",
        direction=direction,  # type: ignore[arg-type]
        crosses_key_handicaps=[],
        snapshots_used=2,
    )


def _consensus(edge: str = "in_line", agreement: str = "high") -> ConsensusReport:
    return ConsensusReport(
        match_id="m1",
        market_type="AH_full",
        n_books=3,
        consensus_line=Decimal("-1.75"),
        user_platform_line=Decimal("-1.75"),
        user_platform_edge=edge,  # type: ignore[arg-type]
        edge_magnitude=Decimal("0"),
        agreement=agreement,  # type: ignore[arg-type]
        outlier_books=[],
    )


def _snapshots(n: int = 2, n_books: int = 2) -> list[OddsSnapshot]:
    meta = SourceMetadata.model_validate({
        "source": "manual",
        "collected_at": datetime(2026, 6, 22, 21, 0, tzinfo=UTC),
        "source_confidence": "high",
        "is_verified": True,
        "notes": "",
    })
    return [
        OddsSnapshot.model_validate({
            "snapshot_id": f"s{i}",
            "match_id": "m1",
            "bookmaker": f"book{i % n_books}",
            "market_type": "AH_full",
            "selection": "home",
            "line": "-1.75",
            "price": "1.92",
            "odds_format": "decimal",
            "metadata": meta.model_dump(mode="json"),
        })
        for i in range(n)
    ]


def test_clean_two_goal_landing_scores_high_into_a_plus():
    s = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness("upgrade"),
        movement=_movement("favorite_strengthening"),
        consensus=_consensus("better_than_market"),
        snapshots=_snapshots(),
    )
    assert s.score >= 80
    assert s.level in ("A", "A+")


def test_skip_harness_drives_low_score():
    s = score_precision(
        classification=_classification(scenario="rotation_trap"),
        corridor=_corridor(1),
        harness=_harness("skip"),
        movement=None,
        consensus=None,
    )
    # Heavy penalties + no harness points + no price-protection scenario
    assert s.level in ("C", "D")


def test_movement_weakening_cuts_movement_channel():
    strong = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness(),
        movement=_movement("favorite_strengthening"),
        consensus=_consensus(),
        snapshots=_snapshots(),
    )
    weak = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness(),
        movement=_movement("favorite_weakening"),
        consensus=_consensus(),
        snapshots=_snapshots(),
    )
    assert weak.channel_scores["movement_support"] < strong.channel_scores["movement_support"]


def test_no_multi_book_penalty():
    s = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness(),
        movement=_movement(),
        consensus=_consensus(),
        snapshots=_snapshots(n=2, n_books=1),  # both snapshots same book
    )
    assert "no_multi_book" in s.penalties_applied


def test_no_time_series_penalty():
    s = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness(),
        movement=None,
        consensus=None,
        snapshots=_snapshots(n=1),
    )
    assert "no_time_series" in s.penalties_applied


def test_market_disagreement_penalty():
    s = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness(),
        movement=_movement(),
        consensus=_consensus(agreement="low"),
        snapshots=_snapshots(),
    )
    assert "market_disagreement" in s.penalties_applied


def test_favorite_qualified_flag_penalises():
    s = score_precision(
        classification=_classification(),
        corridor=_corridor(),
        harness=_harness(),
        movement=_movement(),
        consensus=_consensus(),
        snapshots=_snapshots(),
        risk_flags=["favorite_already_qualified"],
    )
    assert "favorite_already_qualified" in s.penalties_applied


def test_unclear_skip_classification_zeros_clarity():
    s = score_precision(
        classification=_classification(scenario="unclear_skip", conf="0"),
        corridor=_corridor(1),
        harness=_harness("skip"),
        movement=None,
        consensus=None,
    )
    assert s.channel_scores["tiger_clarity"] == 0
