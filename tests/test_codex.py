"""Unit tests for the Codex — the structured invariant registry.

Pure-Python, no LLM, no network. Also acts as a drift guard: proves the
Architect and Reviewer prompts embed the SAME Codex block, which is the whole
point of having one source of truth.
"""

from __future__ import annotations

import pytest

from devswarm import codex


def test_registry_non_empty_and_ids_unique():
    invs = codex.all_invariants()
    assert len(invs) >= 5
    ids = [i.id for i in invs]
    assert len(ids) == len(set(ids)), "invariant ids must be unique"


def test_every_invariant_is_well_formed():
    for inv in codex.all_invariants():
        assert inv.id and inv.id == inv.id.upper()
        assert inv.title.strip()
        assert inv.severity in codex.SEVERITIES
        assert inv.tags, f"{inv.id} must carry at least one tag"
        assert inv.rule.strip()
        assert inv.rationale.strip()
        assert inv.check_hint.strip()


def test_core_invariants_present():
    ids = {i.id for i in codex.all_invariants()}
    for required in ("MONEY-001", "LEDGER-001", "AUDIT-001", "TZ-001", "EXC-001"):
        assert required in ids


def test_money_invariant_is_critical():
    assert codex.get("MONEY-001").severity == "CRITICAL"


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        codex.get("NOPE-999")


def test_select_filters_by_tag():
    money = codex.select(("money",))
    assert money
    assert all("money" in i.tags for i in money)
    # No tags → everything.
    assert codex.select(None) == codex.all_invariants()
    assert codex.select(()) == codex.all_invariants()


def test_render_lists_every_invariant():
    rendered = codex.render_markdown()
    for inv in codex.all_invariants():
        assert inv.id in rendered, f"{inv.id} missing from render"
        assert inv.severity in rendered


def test_codex_version_is_semver_like():
    parts = codex.CODEX_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_architect_and_reviewer_embed_the_same_codex():
    """Single-source-of-truth drift guard: both prompts must contain every
    invariant id, and the version string, so neither can silently diverge."""
    from devswarm.prompts import ARCHITECT_SYSTEM, REVIEWER_SYSTEM

    for inv in codex.all_invariants():
        assert inv.id in ARCHITECT_SYSTEM, f"{inv.id} missing from Architect prompt"
        assert inv.id in REVIEWER_SYSTEM, f"{inv.id} missing from Reviewer prompt"

    assert codex.CODEX_VERSION in ARCHITECT_SYSTEM
    assert codex.CODEX_VERSION in REVIEWER_SYSTEM
