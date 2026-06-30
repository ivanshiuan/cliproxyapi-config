"""CLI: run the Reviewer regression eval with the real model.

    python -m devswarm.evals

Requires ANTHROPIC_API_KEY. Exits non-zero if any golden case regresses, so it
can gate a prompt/Codex change in CI once a key is available.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from ..codex import CODEX_VERSION
from ..config import load_config
from ..llm import make_client
from ..prompts import REVIEWER_PROMPT_VERSION
from .runner import run_all, score

console = Console()


def main() -> int:
    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print(
            "[red]error:[/red] ANTHROPIC_API_KEY not set — the live eval needs "
            "the real Reviewer model. Fill .env and retry."
        )
        return 2

    cfg = load_config()
    client = make_client(cfg)

    console.print(
        f"Running Reviewer eval — model={cfg.model_reviewer} "
        f"reviewer_prompt=v{REVIEWER_PROMPT_VERSION} codex=v{CODEX_VERSION}"
    )
    results = run_all(client, cfg)
    summary = score(results)

    table = Table(title="Reviewer regression eval", show_header=True, header_style="bold cyan")
    table.add_column("case")
    table.add_column("expected")
    table.add_column("actual")
    table.add_column("basis")
    table.add_column("ok")
    for r in results:
        ok = "✅" if r.passed else "❌"
        basis = "—" if r.expected_verdict == "PASS" else ("✓" if r.basis_ok else "✗")
        table.add_row(r.name, r.expected_verdict, r.actual_verdict, basis, ok)
    console.print(table)

    console.print(
        f"verdict {summary['verdict_correct']}/{summary['total']}  "
        f"verdict+basis {summary['verdict_and_basis_correct']}/{summary['total']}"
    )
    if summary["failures"]:
        console.print(f"[red]regressions:[/red] {', '.join(summary['failures'])}")
        return 1
    console.print("[green]all golden cases held ✅[/green]")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
