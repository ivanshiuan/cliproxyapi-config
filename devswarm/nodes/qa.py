"""QA Agent node — runs pytest and (on failure) asks Haiku for a structured diagnosis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..llm import Usage, call as llm_call
from ..prompts import QA_SYSTEM
from ..sandbox import run_pytest
from ..state import QAReport, SwarmState
from ._common import Timer, build_telemetry


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # Drop the first line (``` or ```json) and the trailing fence.
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _parse_json_report(text: str) -> dict[str, Any] | None:
    """Best-effort: parse the QA agent's JSON output, tolerating accidental fences."""
    candidate = _strip_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try to locate the outermost {...} substring.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def qa_node(state: SwarmState, *, client: Any, cfg: Config) -> dict[str, Any]:
    workspace_path = Path(state["workspace_path"])

    with Timer() as t_sandbox:
        sb = run_pytest(workspace_path, timeout_sec=cfg.sandbox_timeout_sec)

    # On pass: no LLM call needed — just construct a clean report.
    if sb["passed"]:
        report: QAReport = {  # type: ignore[typeddict-item]
            "passed": True,
            "exit_code": sb["exit_code"],
            "stdout_tail": sb["stdout_tail"],
            "stderr_tail": sb["stderr_tail"],
            "failed_tests": [],
            "root_cause": "All tests passed.",
            "fix_direction": "No action required.",
        }
        telemetry = build_telemetry(
            node="qa",
            role="qa",
            summary=f"pytest PASSED (exit={sb['exit_code']}, sandbox={t_sandbox.elapsed:.1f}s)",
            usage=Usage(),
            duration_sec=t_sandbox.elapsed,
        )
        return {
            "qa_report": report,
            "tests_passed": True,
            "messages": [telemetry],
            "cost_estimate_usd": state.get("cost_estimate_usd", 0.0),
        }

    # On fail: ask Haiku to diagnose root cause and suggest fix.
    qa_input = (
        f"EXIT_CODE: {sb['exit_code']}\n"
        f"STDOUT:\n{sb['stdout_tail']}\n\n"
        f"STDERR:\n{sb['stderr_tail']}\n"
    )

    with Timer() as t_llm:
        resp = llm_call(
            client,
            cfg,
            model=cfg.model_qa,
            system=QA_SYSTEM,
            messages=[{"role": "user", "content": qa_input}],
            max_tokens=2048,
            temperature=0.0,
            cache_system=True,
        )

    parsed = _parse_json_report(resp.text) or {}

    # Build the QAReport, preferring sandbox truth for mechanical fields and
    # falling back to Haiku for semantic fields.
    report = {  # type: ignore[typeddict-item]
        "passed": False,
        "exit_code": sb["exit_code"],
        "stdout_tail": sb["stdout_tail"],
        "stderr_tail": sb["stderr_tail"],
        "failed_tests": parsed.get("failed_tests") or sb["failed_tests"],
        "root_cause": parsed.get("root_cause") or "(diagnosis unavailable)",
        "fix_direction": parsed.get("fix_direction") or "(no concrete suggestion)",
    }

    total_duration = t_sandbox.elapsed + t_llm.elapsed
    telemetry = build_telemetry(
        node="qa",
        role="qa",
        summary=(
            f"pytest FAILED (exit={sb['exit_code']}, "
            f"{len(report['failed_tests'])} failing, sandbox={t_sandbox.elapsed:.1f}s)"
        ),
        usage=resp.usage,
        duration_sec=total_duration,
    )

    return {
        "qa_report": report,
        "tests_passed": False,
        "messages": [telemetry],
        "cost_estimate_usd": state.get("cost_estimate_usd", 0.0) + resp.usage.cost_usd,
    }
