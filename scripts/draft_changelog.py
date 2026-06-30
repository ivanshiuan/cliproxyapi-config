#!/usr/bin/env python3
"""Changelog drafter.

Reads: git log since last tag (falls back to full history if no tags)
Writes: RELEASE_NOTES_DRAFT.md

Conventional commit prefixes supported: feat/fix/docs/chore/test/refactor/perf
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFT_FILE = ROOT / "RELEASE_NOTES_DRAFT.md"

PREFIX_ORDER = ["feat", "fix", "refactor", "perf", "test", "docs", "chore"]
PREFIX_LABEL = {
    "feat":     "✨ Features",
    "fix":      "🐛 Bug Fixes",
    "refactor": "♻️ Refactor",
    "perf":     "⚡ Performance",
    "test":     "🧪 Tests",
    "docs":     "📝 Docs",
    "chore":    "🔧 Chores",
    "other":    "📦 Other",
}


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT)
    return r.stdout.strip()


def last_tag() -> str | None:
    tag = git("describe", "--tags", "--abbrev=0")
    return tag or None


def commits_since(ref: str) -> list[tuple[str, str]]:
    log = git("log", f"{ref}..HEAD", "--pretty=format:%h\t%s", "--no-merges")
    if not log:
        return []
    result = []
    for line in log.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            result.append((parts[0], parts[1]))
    return result


def categorize(
    commits: list[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    buckets: dict[str, list[tuple[str, str]]] = {k: [] for k in PREFIX_ORDER}
    buckets["other"] = []
    for h, subject in commits:
        match = re.match(r"^(\w+)(?:\([^)]+\))?!?:\s*", subject)
        prefix = match.group(1).lower() if match else "other"
        key = prefix if prefix in buckets else "other"
        buckets[key].append((h, subject))
    return buckets


def strip_prefix(subject: str) -> str:
    return re.sub(r"^\w+(?:\([^)]+\))?!?:\s*", "", subject)


def main() -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tag = last_tag()

    if tag:
        base_ref = tag
        since_label = f"since `{tag}`"
    else:
        base_ref = git("rev-list", "--max-parents=0", "HEAD")
        since_label = "since first commit"

    commits = commits_since(base_ref)

    if not commits:
        print("No new commits since last tag — skipping draft.")
        return

    buckets = categorize(commits)

    lines = [
        "# Release Notes Draft",
        "",
        f"> Auto-generated {now_str} — {len(commits)} commit(s) {since_label}",
        "> **Human review required before publishing.**",
        "",
    ]

    for prefix in PREFIX_ORDER:
        items = buckets[prefix]
        if not items:
            continue
        lines.append(f"## {PREFIX_LABEL[prefix]}")
        for h, subject in items:
            lines.append(f"- {strip_prefix(subject)} (`{h}`)")
        lines.append("")

    if buckets["other"]:
        lines.append(f"## {PREFIX_LABEL['other']}")
        for h, subject in buckets["other"]:
            lines.append(f"- {subject} (`{h}`)")
        lines.append("")

    lines += [
        "---",
        "*Draft by `changelog-drafter` workflow — edit before publishing.*",
    ]

    DRAFT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ RELEASE_NOTES_DRAFT.md — {len(commits)} commits {since_label}")


if __name__ == "__main__":
    main()
