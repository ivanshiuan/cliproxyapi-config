"""SQLite CRUD round-trips."""

from __future__ import annotations

from decimal import Decimal

from tigerline.classifier import classify
from tigerline.recommender import recommend
from tigerline.review import review
from tigerline.storage import (
    latest_plan,
    list_backlog,
    load_match,
    save_classification,
    save_match,
    save_recommendation,
    save_review,
    stats_summary,
)


def test_match_round_trip(db, load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    save_match(db, m)
    back = load_match(db, m.match_id)
    assert back == m


def test_recommendation_round_trip(db, load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    save_match(db, m)
    plan = recommend(m)
    save_recommendation(db, plan, m.bankroll)
    back = latest_plan(db, m.match_id)
    assert back == plan


def test_classification_save(db, load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    save_match(db, m)
    c = classify(m)
    save_classification(db, m.match_id, c)
    row = db.execute(
        "SELECT scenario, confidence FROM classifications WHERE match_id = ?", (m.match_id,)
    ).fetchone()
    assert row["scenario"] == c.scenario
    assert Decimal(row["confidence"]) == c.confidence


def test_review_persists_with_suggestions(db, load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    save_match(db, m)
    save_classification(db, m.match_id, classify(m))
    plan = recommend(m)
    save_recommendation(db, plan, m.bankroll)
    r = review(m, plan, 5, 1)
    save_review(db, r)

    result_row = db.execute(
        "SELECT home_goals, away_goals FROM results WHERE match_id = ?", (m.match_id,)
    ).fetchone()
    assert (result_row["home_goals"], result_row["away_goals"]) == (5, 1)

    if r.suggestions:
        update_rows = db.execute(
            "SELECT suggestion FROM rule_updates WHERE match_id = ?", (m.match_id,)
        ).fetchall()
        assert len(update_rows) == len(r.suggestions)


def test_backlog_excludes_reviewed_matches(db, load_fixture):
    m1 = load_fixture("belgium_nz_two_goal")
    m2 = load_fixture("egypt_iran_pressure_under")
    for m in (m1, m2):
        save_match(db, m)
        save_classification(db, m.match_id, classify(m))
        save_recommendation(db, recommend(m), m.bankroll)

    # Review only m1
    save_review(db, review(m1, recommend(m1), 5, 1))

    backlog = list_backlog(db)
    assert m1.match_id not in backlog
    assert m2.match_id in backlog


def test_stats_summary_buckets_by_scenario(db, load_fixture):
    m = load_fixture("belgium_nz_two_goal")
    save_match(db, m)
    save_classification(db, m.match_id, classify(m))
    plan = recommend(m)
    save_recommendation(db, plan, m.bankroll)
    save_review(db, review(m, plan, 2, 0))

    s = stats_summary(db)
    assert "two_goal_landing" in s
    assert s["two_goal_landing"]["n"] == 1
    assert s["two_goal_landing"]["main_win"] == 1
