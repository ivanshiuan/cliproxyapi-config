"""V3.0 Sprint 6 — CLI subcommand smoke tests via Typer CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tigerline.cli import app

runner = CliRunner()


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch):
    """Each test gets its own SQLite + JSONL — no shared state across tests."""
    monkeypatch.setenv("TIGER_DB_PATH", str(tmp_path / "tiger.db"))
    monkeypatch.setenv("TIGER_SNAPSHOTS_JSONL", str(tmp_path / "snaps.jsonl"))
    yield tmp_path


@pytest.fixture
def seeded_match(isolated_env):
    """Seed the DB with the Belgium-NZ match + 2 snapshots for movement/consensus."""
    repo_root = Path(__file__).resolve().parents[2]
    example = repo_root / "tigerline" / "examples" / "belgium_nz_two_goal.json"
    result = runner.invoke(app, ["analyze", str(example)])
    assert result.exit_code == 0, result.output

    snaps_dir = isolated_env / "snaps"
    snaps_dir.mkdir()

    def _meta(ts: str) -> dict:
        return {
            "source": "manual",
            "collected_at": ts,
            "source_confidence": "high",
            "is_verified": True,
            "notes": "",
        }

    snap_specs = [
        ("snap_a", "pinnacle", "-1.5", "2026-06-22T18:00:00+00:00"),
        ("snap_b", "pinnacle", "-2.0", "2026-06-22T21:00:00+00:00"),
        ("snap_c", "bet365", "-1.75", "2026-06-22T21:00:00+00:00"),
    ]
    for sid, book, line, ts in snap_specs:
        path = snaps_dir / f"{sid}.json"
        path.write_text(json.dumps({
            "snapshot_id": sid,
            "match_id": "2026-wc-belgium-newzealand",
            "bookmaker": book,
            "market_type": "AH_full",
            "selection": "Belgium",
            "line": line,
            "price": "1.92",
            "odds_format": "decimal",
            "metadata": _meta(ts),
        }))
        result = runner.invoke(app, ["snapshot", "add", str(path)])
        assert result.exit_code == 0, result.output

    return isolated_env


def test_top_level_help_lists_v3_subcommands(isolated_env):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("snapshot", "movement", "consensus", "precision", "clv", "report"):
        assert cmd in result.output


def test_movement_analyze_outputs_direction(seeded_match):
    result = runner.invoke(
        app, ["movement", "analyze", "2026-wc-belgium-newzealand", "--market", "AH_full", "--book", "pinnacle"]
    )
    assert result.exit_code == 0, result.output
    assert "favorite_strengthening" in result.output
    assert "-1.5" in result.output and "-2.0" in result.output


def test_movement_analyze_errors_on_too_few_snapshots(isolated_env):
    result = runner.invoke(
        app, ["movement", "analyze", "no-such-match", "--market", "AH_full"]
    )
    assert result.exit_code == 1
    assert "at least 2 snapshots" in result.output or "Need" in result.output


def test_consensus_show_with_user_book(seeded_match):
    result = runner.invoke(
        app, ["consensus", "show", "2026-wc-belgium-newzealand", "--market", "AH_full",
              "--user-book", "bet365"]
    )
    assert result.exit_code == 0, result.output
    assert "Consensus line" in result.output
    assert "better_than_market" in result.output


def test_consensus_show_errors_on_empty(isolated_env):
    result = runner.invoke(
        app, ["consensus", "show", "no-such-match", "--market", "AH_full"]
    )
    assert result.exit_code == 1


def test_precision_score_reports_band(seeded_match):
    result = runner.invoke(
        app, ["precision", "score", "2026-wc-belgium-newzealand",
              "--market", "AH_full", "--user-book", "bet365"]
    )
    assert result.exit_code == 0, result.output
    # Belgium NZ + favorable movement + edge should land in A or A+
    assert "Precision:" in result.output
    assert any(level in result.output for level in ("A+", "A", "B"))


def test_report_daily_renders_disclaimer(seeded_match):
    result = runner.invoke(
        app, ["report", "daily", "2026-wc-belgium-newzealand", "--tier", "pro"]
    )
    assert result.exit_code == 0, result.output
    assert "Pre-Match Brief" in result.output
    # Compliance disclaimer always appended
    assert "保證獲利承諾" in result.output


def test_report_review_settles_score(seeded_match):
    result = runner.invoke(
        app, ["report", "review", "2026-wc-belgium-newzealand", "5-1", "--tier", "pro"]
    )
    assert result.exit_code == 0, result.output
    assert "Post-Match Review" in result.output
    assert "5-1" in result.output


def test_report_calibration_with_no_data(seeded_match):
    result = runner.invoke(app, ["report", "calibration", "--tier", "vip"])
    assert result.exit_code == 0, result.output
    assert "Weekly Calibration" in result.output


def test_report_avoid_for_seeded_match(seeded_match):
    result = runner.invoke(
        app, ["report", "avoid", "2026-wc-belgium-newzealand", "--tier", "basic"]
    )
    assert result.exit_code == 0, result.output
    assert "Avoid List" in result.output


def test_clv_summary_empty(isolated_env):
    result = runner.invoke(app, ["clv", "summary"])
    assert result.exit_code == 0
    assert "No CLV records" in result.output


def test_snapshot_add_then_list(seeded_match):
    result = runner.invoke(
        app, ["snapshot", "list", "2026-wc-belgium-newzealand", "--market", "AH_full"]
    )
    assert result.exit_code == 0
    assert "pinnacle" in result.output
    assert "bet365" in result.output
