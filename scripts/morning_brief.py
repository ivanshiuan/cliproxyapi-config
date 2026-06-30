#!/usr/bin/env python3
"""Morning brief generator.

Reads: git log, specs/, COMMANDER_HANDOFF.md
Writes: LOOP_STATE.md

No DB or API key required — safe to run in GitHub Actions cron.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = ROOT / "specs"
STATE_FILE = ROOT / "LOOP_STATE.md"
HANDOFF_FILE = ROOT / "COMMANDER_HANDOFF.md"


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
    return r.stdout.strip()


def pending_decisions() -> list[str]:
    if not HANDOFF_FILE.exists():
        return []
    return [
        line.strip()
        for line in HANDOFF_FILE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*[-*]\s+\[ \]", line)
    ]


def spec_list() -> list[str]:
    if not SPECS_DIR.exists():
        return []
    return sorted(p.stem for p in SPECS_DIR.glob("*.md"))


def main() -> None:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    taipei_hour = (now.hour + 8) % 24
    now_taipei = f"{taipei_hour:02d}:{now.minute:02d} Taipei"

    commits = git("log", "--oneline", "-7")
    dirty = git("status", "--short")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    specs = spec_list()
    decisions = pending_decisions()

    brief_section = "\n".join([
        f"**Branch:** `{branch}` | **Tree:** {'⚠️ dirty' if dirty else '✅ clean'}",
        "",
        "### Last 7 Commits",
        "```",
        commits or "(none)",
        "```",
        "",
        f"### Specs ({len(specs)} total)",
        *[f"- `{s}`" for s in specs],
        "",
        f"*{now_str} / {now_taipei}*",
    ])

    decisions_section = "\n".join(decisions) if decisions else "_(none found in COMMANDER_HANDOFF.md)_"

    content = "\n".join([
        "# Loop State",
        "",
        "> Auto-updated by `daily-brief` workflow. Do not edit manually.",
        "> Manual trigger: GitHub Actions → Daily Brief → Run workflow.",
        "",
        f"last_run: {now_str}",
        f"pending_specs: {len(specs)}",
        f"open_decisions: {len(decisions)}",
        "",
        "## PR Watch List",
        "",
        "_(updated by PR Babysitter on each PR event)_",
        "",
        "## Pending Decisions",
        "",
        decisions_section,
        "",
        "## Recent Brief",
        "",
        brief_section,
        "",
    ])

    STATE_FILE.write_text(content, encoding="utf-8")
    print(f"✅ LOOP_STATE.md updated — {now_str}")
    print()
    print(brief_section)


if __name__ == "__main__":
    main()
