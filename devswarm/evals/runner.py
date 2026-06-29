"""Run golden cases through the Reviewer node and score the verdicts.

Works with any client exposing the Anthropic ``messages.create`` shape — a real
client (live eval) or a scripted mock (harness wiring test). The Reviewer node
is exercised exactly as it runs in the swarm: files are written into a real
workspace, then ``reviewer_node`` reads them back and adjudicates.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..nodes import reviewer_node
from ..state import SwarmState
from ..workspace import WorkspaceManager
from .cases import GOLDEN_CASES, EvalCase


@dataclass(frozen=True)
class EvalResult:
    """Outcome of adjudicating one golden case."""

    name: str
    expected_verdict: str
    actual_verdict: str
    verdict_ok: bool
    basis_ok: bool  # for BLOCK cases: did findings cite every expected Codex id?
    findings: str

    @property
    def passed(self) -> bool:
        return self.verdict_ok and self.basis_ok


def run_case(
    client: Any, cfg: Config, case: EvalCase, workspace_root: Path
) -> EvalResult:
    """Write the case's files into a fresh workspace and adjudicate via reviewer_node."""
    ws = WorkspaceManager.create(workspace_root, case.name, case.prd)
    for path, content in case.files.items():
        ws.write(path, content)

    state: SwarmState = {  # type: ignore[assignment]
        "workspace_path": str(ws.root),
        "prd": case.prd,
        "security_constraints": list(case.constraints),
        "review_iter": 0,
    }

    out = reviewer_node(state, client=client, cfg=cfg)

    actual = "PASS" if out.get("review_passed") else "BLOCK"
    findings = out.get("review_findings") or ""
    verdict_ok = actual == case.expect_verdict
    if case.expect_verdict == "BLOCK":
        basis_ok = all(b in findings for b in case.expect_basis)
    else:
        basis_ok = True

    return EvalResult(
        name=case.name,
        expected_verdict=case.expect_verdict,
        actual_verdict=actual,
        verdict_ok=verdict_ok,
        basis_ok=basis_ok,
        findings=findings,
    )


def run_all(
    client: Any,
    cfg: Config,
    cases: tuple[EvalCase, ...] = GOLDEN_CASES,
    workspace_root: Path | None = None,
) -> list[EvalResult]:
    """Run every case. Uses a throwaway temp workspace root if none is given."""
    if workspace_root is not None:
        return [run_case(client, cfg, c, workspace_root) for c in cases]
    with tempfile.TemporaryDirectory(prefix="devswarm-eval-") as tmp:
        root = Path(tmp)
        return [run_case(client, cfg, c, root) for c in cases]


def score(results: list[EvalResult]) -> dict[str, Any]:
    """Aggregate results into a summary."""
    total = len(results)
    verdict_hits = sum(r.verdict_ok for r in results)
    full_hits = sum(r.passed for r in results)
    return {
        "total": total,
        "verdict_correct": verdict_hits,
        "verdict_and_basis_correct": full_hits,
        "verdict_rate": (verdict_hits / total) if total else 0.0,
        "full_rate": (full_hits / total) if total else 0.0,
        "failures": [r.name for r in results if not r.passed],
    }
