"""Unit tests for Hermes — the outbound notification layer.

Pure-Python, no LLM, no network: exercises event classification, the notifier
factory, and the three v1 notifier implementations.
"""

from __future__ import annotations

import io

from devswarm.hermes import (
    ConsoleNotifier,
    NullNotifier,
    StubNotifier,
    SwarmEvent,
    build_event,
    make_notifier,
)

# ──────────────────────────────────────────────────────────────────────────
# Terminal-state fixtures (minimal SwarmState snapshots)
# ──────────────────────────────────────────────────────────────────────────


def _succeeded() -> dict:
    return {
        "tests_passed": True,
        "artifacts": [{"path": "m.py"}, {"path": "test_m.py"}, {"path": "m.py"}],
        "cost_estimate_usd": 0.42,
        "heal_iter": 1,
        "review_iter": 1,
        "review_passed": True,
    }


def _budget_halted() -> dict:
    return {
        "tests_passed": False,
        "cost_limit_usd": 0.05,
        "cost_estimate_usd": 0.11,
        "review_passed": True,
        "review_iter": 1,
        "max_review_iters": 3,
        "heal_iter": 1,
        "max_heal_iters": 5,
        "qa_report": {"root_cause": "broken"},
    }


def _review_exhausted() -> dict:
    return {
        "tests_passed": False,
        "review_passed": False,
        "review_iter": 3,
        "max_review_iters": 3,
        "review_findings": "1. [CRITICAL] m.py:f — float money\n   - basis: MONEY-001",
        "heal_iter": 3,
        "max_heal_iters": 5,
        "cost_estimate_usd": 1.0,
    }


def _heal_exhausted() -> dict:
    return {
        "tests_passed": False,
        "review_passed": True,
        "review_iter": 3,
        "max_review_iters": 3,
        "heal_iter": 5,
        "max_heal_iters": 5,
        "qa_report": {"root_cause": "assertion still fails"},
        "cost_estimate_usd": 2.0,
    }


def _failed() -> dict:
    return {
        "tests_passed": False,
        "review_passed": True,
        "review_iter": 1,
        "max_review_iters": 3,
        "heal_iter": 1,
        "max_heal_iters": 5,
        "qa_report": {"root_cause": "one test red"},
        "cost_estimate_usd": 0.3,
    }


# ──────────────────────────────────────────────────────────────────────────
# Classification
# ──────────────────────────────────────────────────────────────────────────


def test_build_event_classifies_success():
    ev = build_event(_succeeded(), "abc123", "/ws/abc123")
    assert ev.kind == "succeeded"
    assert ev.is_success is True
    assert ev.needs_attention is False
    assert "abc123" in ev.summary
    # de-duped artifact count (m.py listed twice).
    assert "2 artifact" in ev.detail


def test_build_event_classifies_budget_halt_before_exhaustion():
    ev = build_event(_budget_halted(), "t", "/ws/t")
    assert ev.kind == "budget_halted"
    assert ev.needs_attention is True
    assert "0.05" in ev.detail


def test_build_event_classifies_review_exhausted():
    ev = build_event(_review_exhausted(), "t", "/ws/t")
    assert ev.kind == "review_exhausted"
    # The latest finding's first line (the headline) is surfaced, terse.
    assert "float money" in ev.detail


def test_build_event_classifies_heal_exhausted():
    ev = build_event(_heal_exhausted(), "t", "/ws/t")
    assert ev.kind == "heal_exhausted"
    assert "assertion still fails" in ev.detail


def test_build_event_classifies_generic_failure():
    ev = build_event(_failed(), "t", "/ws/t")
    assert ev.kind == "failed"
    assert "one test red" in ev.detail


def test_event_carries_counters_and_workspace():
    ev = build_event(_heal_exhausted(), "task9", "/ws/task9")
    assert ev.heal_iter == 5
    assert ev.review_iter == 3
    assert ev.workspace == "/ws/task9"
    assert ev.cost_usd == 2.0


# ──────────────────────────────────────────────────────────────────────────
# Factory + notifiers
# ──────────────────────────────────────────────────────────────────────────


def test_make_notifier_selects_implementation():
    assert isinstance(make_notifier("stub"), StubNotifier)
    assert isinstance(make_notifier("none"), NullNotifier)
    assert isinstance(make_notifier("off"), NullNotifier)
    assert isinstance(make_notifier("console"), ConsoleNotifier)
    # Unknown channel falls back to console rather than silently dropping.
    assert isinstance(make_notifier("typo-here"), ConsoleNotifier)
    assert isinstance(make_notifier(""), ConsoleNotifier)


def test_stub_notifier_captures_events():
    stub = StubNotifier()
    ev = build_event(_succeeded(), "t", "/ws/t")
    stub.notify(ev)
    stub.notify(ev)
    assert len(stub.events) == 2
    assert stub.events[0] is ev


def test_null_notifier_is_silent():
    null = NullNotifier()
    assert null.notify(build_event(_succeeded(), "t", "/ws/t")) is None


def test_console_notifier_writes_summary(capsys=None):
    buf = io.StringIO()
    from rich.console import Console

    notifier = ConsoleNotifier(console=Console(file=buf, width=100, no_color=True))
    notifier.notify(build_event(_failed(), "xyz", "/ws/xyz"))
    out = buf.getvalue()
    assert "hermes" in out
    assert "xyz" in out
    assert "/ws/xyz" in out


def test_swarmevent_is_frozen():
    ev = build_event(_succeeded(), "t", "/ws/t")
    assert isinstance(ev, SwarmEvent)
    try:
        ev.kind = "mutated"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("SwarmEvent should be immutable")
