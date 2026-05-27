"""SwarmState — the single TypedDict flowing through every LangGraph node.

Design notes:
- Flat, JSON-serializable; no LangChain Message objects. Nodes summarize into `messages`.
- List fields use `Annotated[list, operator.add]` so LangGraph merges partial returns.
- Numeric counters like `heal_iter` are *replaced* (default reducer), not added.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict


class Artifact(TypedDict):
    """A file produced by an agent into the workspace."""

    path: str  # relative to workspace/<task_id>/
    kind: Literal["code", "test", "doc", "schema"]
    bytes: int


class QAReport(TypedDict):
    """Structured pytest result, populated by the QA node."""

    passed: bool
    exit_code: int
    stdout_tail: str  # last ~2-4 KB
    stderr_tail: str  # last ~1-2 KB
    failed_tests: list[str]
    root_cause: str  # 1-3 sentence diagnosis (LLM-generated)
    fix_direction: str  # concrete next-step suggestion


class TelemetryMsg(TypedDict, total=False):
    """One log line per node invocation. Aggregated for CLI display and post-mortem."""

    node: str
    role: str  # "pm" | "architect" | "coder" | "qa"
    prompt_version: str  # which system-prompt revision produced this turn
    ts: str  # ISO-8601 UTC
    summary: str
    tokens_in: int
    tokens_out: int
    cache_creation: int
    cache_read: int
    cost_usd: float
    duration_sec: float


class SwarmState(TypedDict, total=False):
    """LangGraph state. `total=False` lets nodes return partial dicts."""

    # --- inputs (set by CLI) ---
    task_id: str  # short uuid4 hex
    user_request: str  # raw commander prompt
    workspace_path: str  # absolute path to workspace/<task_id>/

    # --- PM output ---
    prd: str  # markdown PRD with Goal/Scope/Interface/Acceptance Criteria

    # --- Architect output ---
    arch_spec: str  # markdown architecture spec
    security_constraints: list[str]  # numbered imperatives

    # --- Coder output (accumulates across heal iterations) ---
    artifacts: Annotated[list[Artifact], operator.add]
    coder_notes: str  # latest iteration's rationale

    # --- QA output (replaced each iteration) ---
    qa_report: QAReport
    tests_passed: bool

    # --- Self-heal control ---
    heal_iter: int  # starts at 0, +1 each Coder pass
    max_heal_iters: int  # from config

    # --- Cost guardrails ---
    cost_limit_usd: float  # 0.0 = unlimited; else graph halts when exceeded
    budget_exceeded: bool  # set by routing if cost_estimate_usd > cost_limit_usd

    # --- Telemetry (accumulating) ---
    messages: Annotated[list[TelemetryMsg], operator.add]
    cost_estimate_usd: float  # replaced each step with running total


def initial_state(
    task_id: str,
    user_request: str,
    workspace_path: str,
    max_heal_iters: int,
    cost_limit_usd: float = 0.0,
) -> SwarmState:
    """Construct the seed state injected at graph START.

    ``cost_limit_usd`` of 0.0 disables the budget guard; positive values
    halt the graph as soon as accumulated cost exceeds the limit.
    """
    return SwarmState(
        task_id=task_id,
        user_request=user_request,
        workspace_path=workspace_path,
        prd="",
        arch_spec="",
        security_constraints=[],
        artifacts=[],
        coder_notes="",
        tests_passed=False,
        heal_iter=0,
        max_heal_iters=max_heal_iters,
        cost_limit_usd=cost_limit_usd,
        budget_exceeded=False,
        messages=[],
        cost_estimate_usd=0.0,
    )
