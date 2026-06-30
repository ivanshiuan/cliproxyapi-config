"""DevSwarm CLI entrypoint.

    python -m devswarm "<request>"
    python -m devswarm --task-file specs/profit_calc.md
    python -m devswarm "<request>" --max-heal 3 --verbose

Exit codes:
    0   tests_passed (full success)
    1   tests_failed (after max-heal iterations)
    2   bad arguments / missing API key
    3   internal failure (unhandled exception during graph run)
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .graph import build_graph
from .hermes import build_event, make_notifier
from .llm import make_client
from .state import initial_state
from .workspace import WorkspaceManager

console = Console()


# ──────────────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devswarm",
        description="LangGraph-based AI agent swarm. PM → Architect → Coder → QA self-heal.",
    )
    p.add_argument(
        "request",
        nargs="?",
        default=None,
        help="Natural-language task brief. Mutually exclusive with --task-file.",
    )
    p.add_argument(
        "--task-file",
        type=Path,
        default=None,
        help="Path to a Markdown file containing the task brief.",
    )
    p.add_argument(
        "--max-heal",
        type=int,
        default=None,
        help="Override the self-heal loop limit (default: 5 or DEVSWARM_MAX_HEAL_ITERS).",
    )
    p.add_argument(
        "--max-review",
        type=int,
        default=None,
        help="Override the adversarial review loop limit (default: 3 or DEVSWARM_MAX_REVIEW_ITERS).",
    )
    p.add_argument(
        "--budget",
        type=float,
        default=0.0,
        metavar="USD",
        help=(
            "Hard USD ceiling. Graph halts before the next Coder iteration "
            "once cumulative cost exceeds this. 0 = unlimited (default)."
        ),
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Override the workspace root directory (default: ./workspace).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Stream per-node telemetry as the graph runs.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run PM + Architect only; skip Coder/QA. Useful for inspecting plans.",
    )
    return p


def _load_request(args: argparse.Namespace) -> str:
    if args.request and args.task_file:
        console.print(
            "[red]error:[/red] specify either a positional request OR --task-file, not both"
        )
        sys.exit(2)
    if args.task_file:
        if not args.task_file.exists():
            console.print(f"[red]error:[/red] task file not found: {args.task_file}")
            sys.exit(2)
        return args.task_file.read_text(encoding="utf-8")
    if args.request:
        return args.request
    console.print("[red]error:[/red] provide a request or --task-file. See --help.")
    sys.exit(2)


# ──────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ──────────────────────────────────────────────────────────────────────────


def _node_label(node: str) -> str:
    return {
        "pm": "🧭 PM",
        "architect": "🏛  Architect",
        "coder": "⌨  Coder",
        "reviewer": "🔍 Reviewer",
        "qa": "🧪 QA",
    }.get(node, node)


def _render_telemetry(msg: dict[str, Any]) -> None:
    cache_hit_pct = 0.0
    total_in = (msg.get("tokens_in") or 0) + (msg.get("cache_read") or 0) + (
        msg.get("cache_creation") or 0
    )
    if total_in:
        cache_hit_pct = 100.0 * (msg.get("cache_read") or 0) / total_in
    console.print(
        f"  [dim]→[/dim] {_node_label(msg.get('role', ''))}  "
        f"[cyan]{msg.get('summary', '')}[/cyan]  "
        f"[dim]tok in={msg.get('tokens_in', 0)} out={msg.get('tokens_out', 0)} "
        f"cache_read={msg.get('cache_read', 0)} ({cache_hit_pct:.0f}%) "
        f"${msg.get('cost_usd', 0):.4f} {msg.get('duration_sec', 0):.1f}s[/dim]"
    )


def _print_final_summary(final_state: dict[str, Any], task_id: str, workspace_dir: Path) -> None:
    passed = final_state.get("tests_passed", False)
    heal_iter = final_state.get("heal_iter", 0)
    cost = final_state.get("cost_estimate_usd", 0.0)
    limit = final_state.get("cost_limit_usd", 0.0)
    artifacts = final_state.get("artifacts") or []

    budget_halted = not passed and limit > 0 and cost >= limit

    if passed:
        status_color, status_text = "green", "PASSED ✅"
    elif budget_halted:
        status_color, status_text = "yellow", "BUDGET HALTED 💰"
    else:
        status_color, status_text = "red", "FAILED ❌"

    body = Table.grid(padding=(0, 1))
    body.add_column(style="bold")
    body.add_column()
    body.add_row("status:", f"[{status_color}]{status_text}[/{status_color}]")
    body.add_row("task_id:", task_id)
    body.add_row("workspace:", str(workspace_dir))
    body.add_row("heal iterations used:", str(heal_iter))
    cost_row = f"${cost:.4f} USD"
    if limit > 0:
        cost_row += f" (limit ${limit:.2f})"
    body.add_row("total cost (est.):", cost_row)
    body.add_row("artifacts written:", str(len({a['path'] for a in artifacts})))

    console.print(Panel(body, title="DevSwarm run summary", border_style=status_color))

    # Unique-by-path artifact list (latest entry wins).
    seen: dict[str, dict[str, Any]] = {}
    for a in artifacts:
        seen[a["path"]] = a
    if seen:
        table = Table(title="Artifacts", show_header=True, header_style="bold cyan")
        table.add_column("path")
        table.add_column("kind")
        table.add_column("bytes", justify="right")
        for path, a in sorted(seen.items()):
            table.add_row(path, a["kind"], str(a["bytes"]))
        console.print(table)

    if not passed:
        qa = final_state.get("qa_report") or {}
        console.print(Panel(
            f"[bold]root cause:[/bold] {qa.get('root_cause', '(n/a)')}\n\n"
            f"[bold]fix direction:[/bold] {qa.get('fix_direction', '(n/a)')}\n\n"
            f"[bold]failed tests:[/bold] {', '.join(qa.get('failed_tests') or []) or '(none reported)'}",
            title="Final QA report",
            border_style="red",
        ))


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)

    request = _load_request(args)

    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print(
            "[red]error:[/red] ANTHROPIC_API_KEY not set. "
            "Copy .env.example to .env and fill it in."
        )
        return 2

    cfg = load_config(workspace_root=args.workspace_root)
    if args.max_heal is not None or args.max_review is not None:
        # Recreate with the override(s).
        from dataclasses import replace

        overrides: dict[str, Any] = {}
        if args.max_heal is not None:
            overrides["max_heal_iters"] = max(0, args.max_heal)
        if args.max_review is not None:
            overrides["max_review_iters"] = max(0, args.max_review)
        cfg = replace(cfg, **overrides)

    task_id = uuid.uuid4().hex[:8]
    cfg.workspace_root.mkdir(parents=True, exist_ok=True)
    ws = WorkspaceManager.create(cfg.workspace_root, task_id, request)

    budget_display = f"USD ${args.budget:.2f}" if args.budget > 0 else "unlimited"
    console.print(Panel.fit(
        f"[bold]task_id:[/bold] {task_id}\n"
        f"[bold]workspace:[/bold] {ws.root}\n"
        f"[bold]models:[/bold] pm={cfg.model_pm} architect={cfg.model_architect} "
        f"coder={cfg.model_coder} reviewer={cfg.model_reviewer} qa={cfg.model_qa}\n"
        f"[bold]max heal:[/bold] {cfg.max_heal_iters}    "
        f"[bold]max review:[/bold] {cfg.max_review_iters}    "
        f"[bold]budget:[/bold] {budget_display}",
        title="DevSwarm starting",
        border_style="cyan",
    ))

    client = make_client(cfg)
    app = build_graph(client, cfg)

    seed = initial_state(
        task_id=task_id,
        user_request=request,
        workspace_path=str(ws.root),
        max_heal_iters=cfg.max_heal_iters,
        cost_limit_usd=max(0.0, args.budget),
        max_review_iters=cfg.max_review_iters,
    )

    if args.dry_run:
        # Manually run only pm + architect for inspection.
        from .nodes import architect_node, pm_node

        s: dict[str, Any] = dict(seed)
        for fn, _label in [(pm_node, "pm"), (architect_node, "architect")]:
            delta = fn(s, client=client, cfg=cfg)  # type: ignore[arg-type]
            for k, v in delta.items():
                if k == "messages":
                    s.setdefault("messages", []).extend(v)
                    if args.verbose:
                        for m in v:
                            _render_telemetry(m)
                elif k == "cost_estimate_usd":
                    s[k] = v
                else:
                    s[k] = v
        console.print(Panel(s.get("prd", "")[:2000], title="PRD (truncated)", border_style="blue"))
        console.print(
            Panel(s.get("arch_spec", "")[:2000], title="Architecture Spec (truncated)", border_style="blue")
        )
        console.print(
            f"\nDry-run cost: ${s.get('cost_estimate_usd', 0):.4f} USD"
        )
        return 0

    try:
        # Increase the LangGraph recursion limit so we can actually use all heal iters.
        # Default is 25; one full loop is ~4 steps, so 5 heals ≈ ~30 hops worst case.
        config: dict[str, Any] = {"recursion_limit": 64}

        if args.verbose:
            final_state: dict[str, Any] = dict(seed)
            seen_msgs = 0
            for chunk in app.stream(seed, config=config, stream_mode="values"):
                final_state = chunk  # latest snapshot
                msgs = final_state.get("messages") or []
                while seen_msgs < len(msgs):
                    _render_telemetry(msgs[seen_msgs])
                    seen_msgs += 1
        else:
            final_state = app.invoke(seed, config=config)

    except Exception as e:
        console.print(f"[red]Graph execution failed:[/red] {type(e).__name__}: {e}")
        return 3

    _print_final_summary(final_state, task_id, ws.root)

    # Hermes — push a terminal notification. Must never break the run.
    try:
        notifier = make_notifier(cfg.notify_channel, console=console)
        notifier.notify(build_event(final_state, task_id, str(ws.root)))
    except Exception as e:  # pragma: no cover - defensive; notification is best-effort
        console.print(f"[yellow]warning:[/yellow] Hermes notification failed: {type(e).__name__}: {e}")

    return 0 if final_state.get("tests_passed") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
