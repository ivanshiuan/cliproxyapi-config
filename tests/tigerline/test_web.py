"""Sprint 7 — Web layer smoke tests.

Uses FastAPI's TestClient (starlette-backed) for sync request/response
round-trips. This is the *web* layer only — we don't re-verify the
classifier/recommender here; those have their own suites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tigerline.web import app

client = TestClient(app)

_EXAMPLES = Path(__file__).resolve().parents[2] / "tigerline" / "examples"


def _load_example(name: str) -> dict:
    return json.loads((_EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "tigerline"


def test_index_serves_html():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "TIGER LINE PRIME" in r.text


def test_examples_list_includes_belgium():
    r = client.get("/api/examples")
    assert r.status_code == 200
    body = r.json()
    assert "belgium_nz_two_goal" in body["examples"]
    # No stray snapshot fixture leaking through
    assert "snapshot_belgium_t1h" not in body["examples"]


def test_examples_get_returns_matchinput():
    r = client.get("/api/examples/belgium_nz_two_goal")
    assert r.status_code == 200
    body = r.json()
    assert body["match_id"] == "2026-wc-belgium-newzealand"
    assert body["home"] == "Belgium"


def test_examples_get_rejects_traversal():
    r = client.get("/api/examples/..%2F..%2Fetc%2Fpasswd")
    # Either 400 (our guard) or 404 (path stem doesn't match), both fine —
    # what matters is we never serve arbitrary filesystem content.
    assert r.status_code in (400, 404)


def test_examples_get_404():
    r = client.get("/api/examples/does_not_exist")
    assert r.status_code == 404


def test_analyze_belgium_two_goal_landing():
    match = _load_example("belgium_nz_two_goal")
    r = client.post("/api/analyze", json=match)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"] == "two_goal_landing"
    assert body["harness"] in ("upgrade", "normal", "downgrade")
    assert body["plan"]["main_bet"] is not None
    assert body["plan"]["main_bet"]["selection"] == "Belgium -1.5"
    # Corridor primary should be a 2-elem list of ints
    assert len(body["corridor_primary"]) == 2
    # Bilingual disclaimer must be present
    assert "保證獲利承諾" in body["disclaimer"]
    assert "guarantee of profit" in body["disclaimer"]


def test_analyze_rotation_trap_skip():
    match = _load_example("rotation_trap_skip")
    r = client.post("/api/analyze", json=match)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"] == "rotation_trap"
    assert body["harness"] == "skip"
    assert body["plan"]["main_bet"] is None
    assert body["plan"]["avoid"], "rotation_trap must expose an avoid list"


def test_analyze_pressure_under():
    match = _load_example("egypt_iran_pressure_under")
    r = client.post("/api/analyze", json=match)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"] == "pressure_under"
    assert body["plan"]["main_bet"]["selection"].lower().startswith("under")


def test_analyze_rejects_float_line():
    """Float in ``market.line`` must be rejected by StrictDecimal."""
    match = _load_example("belgium_nz_two_goal")
    match["market"]["line"] = -1.75  # bare float, not string
    r = client.post("/api/analyze", json=match)
    assert r.status_code == 422


def test_analyze_rejects_missing_fields():
    r = client.post("/api/analyze", json={"match_id": "x"})
    assert r.status_code == 422


def test_review_belgium_5_1():
    """Full analyze → review round-trip."""
    match = _load_example("belgium_nz_two_goal")
    analyze = client.post("/api/analyze", json=match).json()
    plan = analyze["plan"]
    r = client.post(
        "/api/review",
        json={"match": match, "plan": plan, "home_goals": 5, "away_goals": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    result = body["result"]
    assert result["actual_score"] == [5, 1]
    assert result["scenario_correct"] is True
    assert result["main_result"] == "win"  # Belgium -1.5 wins 5-1
    assert result["score"] >= 60


def test_review_rejects_bad_score():
    match = _load_example("belgium_nz_two_goal")
    analyze = client.post("/api/analyze", json=match).json()
    r = client.post(
        "/api/review",
        json={"match": match, "plan": analyze["plan"], "home_goals": -1, "away_goals": 0},
    )
    assert r.status_code == 422


def test_cors_preflight_ok():
    r = client.options(
        "/api/analyze",
        headers={
            "origin": "https://example.com",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    # Starlette CORSMiddleware returns 200 on successful preflight
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


@pytest.mark.parametrize(
    "name", ["belgium_nz_two_goal", "egypt_iran_pressure_under", "rotation_trap_skip"]
)
def test_all_bundled_examples_analyzable(name):
    """Every bundled example must round-trip through /api/analyze cleanly."""
    match = _load_example(name)
    r = client.post("/api/analyze", json=match)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"], f"empty scenario for {name}"
