"""Run a DevSwarm spec across multiple Coder models in parallel; report comparison.

Stolen from openai/gpt-5-coding-examples' `asteroid-game` vs `asteroid-game-5.2`
side-by-side: the cheapest signal of "which model is good enough for this spec"
is to run the *same* prompt against several model tiers and diff the outputs.

For us, the practical question is cost: if Sonnet 4.6 or Haiku 4.5 passes a
spec's tests at 20% the price of Opus 4.7, that spec should be downgraded.

Usage:
    python scripts/bakeoff.py specs/profit_calc.md
    python scripts/bakeoff.py specs/foo.md --models opus,sonnet
    python scripts/bakeoff.py specs/foo.md --budget 3.0 --max-heal 3

Outputs:
    workspace/bakeoff/<spec_id>/<model>/      — full DevSwarm workspace per model
    workspace/bakeoff/<spec_id>/bakeoff.md    — comparison report

The harness runs each model in a separate subprocess so DevSwarm's own logging
and workspace isolation work unchanged. Parallelism is bounded by --concurrency
(default = min(3, len(models))).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = REPO_ROOT / "workspace" / "bakeoff"

MODEL_IDS = {
    "opus":   "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}


@dataclass
class RunResult:
    model: str
    model_id: str
    workspace: Path
    exit_code: int
    duration_sec: float
    task_id: str | None = None
    tests_passed: bool | None = None
    cost_usd: float | None = None
    heal_iters: int | None = None
    artifacts: list[str] | None = None
    qa_root_cause: str | None = None
    error: str | None = None


def _load_state(workspace: Path) -> dict | None:
    """DevSwarm doesn't dump final state by default; recover what we can from task.json."""
    manifest = workspace / "task.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_stdout_summary(stdout: str) -> dict:
    """Best-effort extract of cost / heal / tests_passed from the CLI's panel output."""
    out: dict = {}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("task_id:"):
            out["task_id"] = line.split(":", 1)[1].strip()
        elif "PASSED" in line and "status" in line.lower():
            out["tests_passed"] = True
        elif "FAILED" in line and "status" in line.lower():
            out["tests_passed"] = False
        elif "BUDGET HALTED" in line:
            out["tests_passed"] = False
            out["budget_halted"] = True
        elif "heal iterations used:" in line:
            try:
                out["heal_iters"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif "total cost" in line and "$" in line:
            # e.g. "total cost (est.):  $1.2345 USD (limit $5.00)"
            try:
                dollars = line.split("$", 1)[1].split()[0]
                out["cost_usd"] = float(dollars)
            except (IndexError, ValueError):
                pass
    return out


def _run_one(spec_path: Path, model: str, budget: float, max_heal: int | None) -> RunResult:
    model_id = MODEL_IDS[model]
    spec_stem = spec_path.stem
    workspace = WORKSPACE_ROOT / spec_stem / model
    workspace.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["DEVSWARM_MODEL_CODER"] = model_id
    # Keep PM/Architect/QA at their defaults so the only varying axis is Coder.

    cmd = [
        sys.executable,
        "-m", "devswarm",
        "--task-file", str(spec_path),
        "--workspace-root", str(workspace.parent),  # devswarm makes <root>/<task_id>/
        "--budget", str(budget),
    ]
    if max_heal is not None:
        cmd += ["--max-heal", str(max_heal)]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd, env=env, cwd=str(REPO_ROOT),
            capture_output=True, text=True, check=False, timeout=1800,
        )
        duration = time.time() - start
    except subprocess.TimeoutExpired:
        return RunResult(
            model=model, model_id=model_id, workspace=workspace,
            exit_code=124, duration_sec=time.time() - start,
            error="timeout after 30 minutes",
        )
    except FileNotFoundError as e:
        return RunResult(
            model=model, model_id=model_id, workspace=workspace,
            exit_code=127, duration_sec=time.time() - start,
            error=f"python/devswarm not found: {e}",
        )

    parsed = _parse_stdout_summary(proc.stdout)
    task_id = parsed.get("task_id")
    actual_ws = workspace.parent / task_id if task_id else workspace
    state = _load_state(actual_ws) if task_id else None
    artifacts = sorted(
        p.name for p in actual_ws.iterdir() if p.is_file() and p.name != "task.json"
    ) if actual_ws.exists() else []

    return RunResult(
        model=model, model_id=model_id, workspace=actual_ws,
        exit_code=proc.returncode, duration_sec=duration,
        task_id=task_id,
        tests_passed=parsed.get("tests_passed"),
        cost_usd=parsed.get("cost_usd"),
        heal_iters=parsed.get("heal_iters"),
        artifacts=artifacts,
        qa_root_cause=(state or {}).get("qa_root_cause") if state else None,
        error=(proc.stderr.strip().splitlines()[-1] if proc.returncode != 0 and proc.stderr.strip() else None),
    )


def _render_report(spec_path: Path, results: list[RunResult]) -> str:
    spec_stem = spec_path.stem
    lines = [
        f"# Bakeoff: `{spec_path.relative_to(REPO_ROOT)}`",
        "",
        f"Ran {len(results)} model(s) against the same spec, varying only `DEVSWARM_MODEL_CODER`.",
        f"PM/Architect/QA defaults left unchanged.",
        "",
        "## Results",
        "",
        "| model | passed | cost USD | heal | duration | artifacts | task_id |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (x.tests_passed is not True, x.cost_usd or 1e9)):
        passed = "✅" if r.tests_passed else ("❌" if r.tests_passed is False else "?")
        cost = f"${r.cost_usd:.4f}" if r.cost_usd is not None else "?"
        heal = str(r.heal_iters) if r.heal_iters is not None else "?"
        dur = f"{r.duration_sec:.1f}s"
        arts = ", ".join(r.artifacts or []) or "—"
        tid = r.task_id or "?"
        lines.append(f"| `{r.model}` ({r.model_id}) | {passed} | {cost} | {heal} | {dur} | {arts} | `{tid}` |")

    lines += ["", "## Verdict", ""]
    winners = [r for r in results if r.tests_passed]
    if not winners:
        lines.append("**No model passed.** Spec is likely under-specified or AC too strict — review before re-running.")
    else:
        cheapest = min((r for r in winners if r.cost_usd is not None), key=lambda x: x.cost_usd, default=None)
        if cheapest:
            lines.append(
                f"**Cheapest passing model: `{cheapest.model}` at ${cheapest.cost_usd:.4f}.**"
            )
            opus = next((r for r in results if r.model == "opus"), None)
            if opus and opus.cost_usd and cheapest.model != "opus":
                save_pct = 100 * (1 - cheapest.cost_usd / opus.cost_usd)
                lines.append(
                    f"Saves {save_pct:.0f}% vs Opus — update spec frontmatter "
                    f"`preferred_model: {cheapest.model}`."
                )
        else:
            lines.append(f"Passing model(s): {', '.join(r.model for r in winners)} (cost unknown — check workspace).")

    failed = [r for r in results if r.tests_passed is False]
    if failed:
        lines += ["", "## Failures", ""]
        for r in failed:
            lines.append(f"- **{r.model}**: exit={r.exit_code}" + (f", root_cause={r.qa_root_cause!r}" if r.qa_root_cause else ""))
            if r.error:
                lines.append(f"  - stderr: `{r.error}`")

    lines += [
        "", "## Files",
        "",
        *[f"- `{r.workspace.relative_to(REPO_ROOT)}/`" for r in results if r.workspace.exists()],
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec", type=Path, help="path to specs/<id>.md")
    p.add_argument("--models", default="opus,sonnet,haiku",
                   help="comma-separated subset of opus,sonnet,haiku (default: all 3)")
    p.add_argument("--budget", type=float, default=5.0, help="per-run USD ceiling (default 5.0)")
    p.add_argument("--max-heal", type=int, default=None, help="per-run heal iteration cap")
    p.add_argument("--concurrency", type=int, default=None, help="parallel runs (default min(3, len(models)))")
    p.add_argument("--skip-validate", action="store_true", help="don't run validate_spec.py first")
    args = p.parse_args(argv)

    if not args.spec.exists():
        print(f"error: spec not found: {args.spec}", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in MODEL_IDS]
    if unknown:
        print(f"error: unknown models {unknown}; valid: {sorted(MODEL_IDS)}", file=sys.stderr)
        return 2

    if not args.skip_validate:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from validate_spec import validate  # type: ignore[import-not-found]
        rep = validate(args.spec)
        if not rep.ok:
            print(f"❌ spec validation failed for {args.spec}:")
            for e in rep.errors:
                print(f"   - {e}")
            print("Re-run with --skip-validate to bypass, but don't.")
            return 1

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY not set in environment.", file=sys.stderr)
        return 2

    concurrency = args.concurrency or min(3, len(models))
    print(f"🏁 bakeoff: {args.spec.name} × {models} (budget ${args.budget}/run, concurrency={concurrency})")
    print()

    results: list[RunResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {
            ex.submit(_run_one, args.spec, m, args.budget, args.max_heal): m
            for m in models
        }
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results.append(r)
            tag = "✅" if r.tests_passed else "❌"
            print(f"  {tag} {r.model}: exit={r.exit_code}, cost=${r.cost_usd or 0:.4f}, {r.duration_sec:.1f}s")

    spec_stem = args.spec.stem
    report_path = WORKSPACE_ROOT / spec_stem / "bakeoff.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(args.spec, results), encoding="utf-8")
    print()
    print(f"📊 report: {report_path.relative_to(REPO_ROOT)}")

    return 0 if any(r.tests_passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
