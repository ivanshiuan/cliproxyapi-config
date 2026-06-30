"""Tests for the Reviewer regression eval harness.

- Structural: the golden set is well-formed and its bases are real Codex ids.
- Wiring (mocked, no key): run_case + score work end to end, AND actually detect
  a regression (a harness that always passes is worthless).
- Live (skipped without ANTHROPIC_API_KEY): the real Reviewer holds every case.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from devswarm import codex
from devswarm.config import load_config
from devswarm.evals import GOLDEN_CASES, run_all, run_case, score

# ── fake client helpers (mirror tests/test_graph_mock.py) ──


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=50,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _verdict_json(verdict: str, basis: str | None = None) -> str:
    findings = []
    if verdict == "BLOCK":
        findings = [{
            "severity": "CRITICAL",
            "location": "m.py:f",
            "issue": "defect",
            "basis": basis or "MONEY-001",
        }]
    return json.dumps({"verdict": verdict, "findings": findings, "advisory": []})


def _mock_client(payloads: list[str]) -> MagicMock:
    it = iter(_resp(p) for p in payloads)
    client = MagicMock()
    client.messages.create = MagicMock(side_effect=lambda **_: next(it))
    return client


# ── structural ──


def test_golden_set_is_well_formed():
    assert GOLDEN_CASES
    codex_ids = {i.id for i in codex.all_invariants()}
    for c in GOLDEN_CASES:
        assert c.files, f"{c.name} has no files"
        assert any(p.endswith(".py") and not p.startswith("test_") for p in c.files)
        assert c.expect_verdict in ("PASS", "BLOCK")
        if c.expect_verdict == "BLOCK":
            assert c.expect_basis, f"{c.name} must name the expected invariant"
            for b in c.expect_basis:
                assert b in codex_ids, f"{c.name} cites unknown invariant {b}"
        else:
            assert not c.expect_basis


# ── wiring: positive (harness scores correct verdicts as pass) ──


def test_harness_scores_correct_verdicts(tmp_path: Path):
    cfg = replace(load_config(workspace_root=tmp_path), max_retries=0)
    # Script the "correct" verdict for each golden case, in order.
    payloads = [
        _verdict_json(c.expect_verdict, c.expect_basis[0] if c.expect_basis else None)
        for c in GOLDEN_CASES
    ]
    client = _mock_client(payloads)

    results = run_all(client, cfg, workspace_root=tmp_path)
    summary = score(results)

    assert summary["total"] == len(GOLDEN_CASES)
    assert summary["full_rate"] == 1.0
    assert not summary["failures"]


# ── wiring: negative (harness must DETECT a regression) ──


def test_harness_detects_a_regression(tmp_path: Path):
    cfg = replace(load_config(workspace_root=tmp_path), max_retries=0)
    bad_case = GOLDEN_CASES[0]  # float_money_blocked → expects BLOCK
    assert bad_case.expect_verdict == "BLOCK"
    # Reviewer wrongly PASSes it → harness must report a failure.
    client = _mock_client([_verdict_json("PASS")])

    result = run_case(client, cfg, bad_case, tmp_path)
    assert result.actual_verdict == "PASS"
    assert result.verdict_ok is False
    assert result.passed is False


def test_harness_detects_wrong_basis(tmp_path: Path):
    cfg = replace(load_config(workspace_root=tmp_path), max_retries=0)
    bad_case = GOLDEN_CASES[0]  # expects basis MONEY-001
    # Right verdict (BLOCK) but cites the wrong invariant → basis_ok False.
    client = _mock_client([_verdict_json("BLOCK", "TZ-001")])

    result = run_case(client, cfg, bad_case, tmp_path)
    assert result.actual_verdict == "BLOCK"
    assert result.verdict_ok is True
    assert result.basis_ok is False
    assert result.passed is False


# ── live (real model) ──


# Live evals are OPT-IN, not merely key-gated: a key present in the ambient env
# (CI, this sandbox) must not make `pytest` silently spend money on every run.
# Run with: DEVSWARM_RUN_LIVE_EVALS=1 pytest tests/test_reviewer_eval.py  (or
# `python -m devswarm.evals`). Requires ANTHROPIC_API_KEY too.
@pytest.mark.skipif(
    not (os.getenv("DEVSWARM_RUN_LIVE_EVALS") and os.getenv("ANTHROPIC_API_KEY")),
    reason="live Reviewer eval is opt-in: needs DEVSWARM_RUN_LIVE_EVALS=1 AND ANTHROPIC_API_KEY",
)
def test_live_reviewer_holds_golden_set(tmp_path: Path):
    from devswarm.llm import make_client

    cfg = load_config(workspace_root=tmp_path)
    client = make_client(cfg)
    results = run_all(client, cfg, workspace_root=tmp_path)
    summary = score(results)
    # Allow a little slack on basis citation, but verdicts must all be right.
    assert summary["verdict_rate"] == 1.0, summary
    assert summary["full_rate"] >= 0.75, summary
