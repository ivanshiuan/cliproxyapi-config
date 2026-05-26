"""Smoke tests for SwarmState reducers and initial_state."""

from __future__ import annotations

from devswarm.state import initial_state


def test_initial_state_has_required_keys():
    s = initial_state(
        task_id="abcd1234",
        user_request="Build something",
        workspace_path="/tmp/ws/abcd1234",
        max_heal_iters=5,
    )

    assert s["task_id"] == "abcd1234"
    assert s["user_request"] == "Build something"
    assert s["workspace_path"] == "/tmp/ws/abcd1234"
    assert s["max_heal_iters"] == 5
    assert s["heal_iter"] == 0
    assert s["tests_passed"] is False
    assert s["artifacts"] == []
    assert s["messages"] == []
    assert s["security_constraints"] == []
    assert s["cost_estimate_usd"] == 0.0
    assert s["cost_limit_usd"] == 0.0  # default = unlimited
    assert s["budget_exceeded"] is False


def test_initial_state_with_budget():
    s = initial_state(
        task_id="b1",
        user_request="x",
        workspace_path="/tmp/b1",
        max_heal_iters=5,
        cost_limit_usd=2.50,
    )
    assert s["cost_limit_usd"] == 2.50


def test_initial_state_defaults_are_independent():
    """Two initial states must not share mutable defaults."""
    a = initial_state("aaaa", "x", "/tmp/a", 1)
    b = initial_state("bbbb", "y", "/tmp/b", 2)
    a["artifacts"].append({"path": "x.py", "kind": "code", "bytes": 10})
    assert b["artifacts"] == []
    assert a["max_heal_iters"] == 1
    assert b["max_heal_iters"] == 2
