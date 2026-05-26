"""List all DevSwarm task specs and their execution status.

Reads `specs/*.md` and `workspace/<task_id>/task.json` to determine which
specs have been run, which are still pending, and the latest run outcome
for each.

Usage:
    python scripts/backlog.py
    python scripts/backlog.py --json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
WORKSPACE_DIR = REPO_ROOT / "workspace"


def _spec_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not SPECS_DIR.exists():
        return entries
    for path in sorted(SPECS_DIR.glob("*.md")):
        body = path.read_text(encoding="utf-8", errors="replace")
        # First-line heading is the friendly name.
        first_line = next((ln for ln in body.splitlines() if ln.strip()), path.stem)
        title = first_line.lstrip("# ").strip() or path.stem
        entries.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "name": path.stem,
                "title": title,
                "size_kb": round(len(body) / 1024, 1),
                "ac_count": body.count("AC-"),
            }
        )
    return entries


def _workspace_runs() -> dict[str, list[dict[str, Any]]]:
    """Map spec name → list of (task_id, request, created_at) runs derived from task.json."""
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not WORKSPACE_DIR.exists():
        return runs
    for manifest in WORKSPACE_DIR.glob("*/task.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        request = data.get("user_request", "")
        # Heuristic: if the request is a markdown body that mentions a spec
        # filename or contains `Module name: <name>.py`, match it. Otherwise
        # bucket under "ad-hoc".
        matched = "ad-hoc"
        for entry in _spec_entries():
            if entry["name"] in request or entry["title"] in request:
                matched = entry["name"]
                break
        runs[matched].append(
            {
                "task_id": data.get("task_id"),
                "created_at": data.get("created_at"),
                "workspace": str(manifest.parent.relative_to(REPO_ROOT)),
                "produced_files": [
                    p.name
                    for p in manifest.parent.iterdir()
                    if p.is_file() and p.name != "task.json"
                ],
            }
        )
    return runs


def render_table(specs: list[dict[str, Any]], runs: dict[str, list[dict[str, Any]]]) -> str:
    headers = ["spec", "title", "ACs", "size", "runs", "latest"]
    rows: list[list[str]] = []
    for s in specs:
        rs = runs.get(s["name"], [])
        latest = rs[-1]["created_at"][:19] if rs else "—"
        rows.append(
            [
                s["name"],
                (s["title"][:55] + "…") if len(s["title"]) > 55 else s["title"],
                str(s["ac_count"]),
                f"{s['size_kb']}KB",
                str(len(rs)) if rs else "0",
                latest,
            ]
        )

    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)] if rows else [
        len(h) for h in headers
    ]
    sep = "  "
    out = [sep.join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    out.append(sep.join("─" * w for w in widths))
    out.extend(sep.join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows)
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    args = parser.parse_args()

    specs = _spec_entries()
    runs = _workspace_runs()

    if args.json:
        payload = {
            "specs": [{**s, "runs": runs.get(s["name"], [])} for s in specs],
            "ad_hoc_runs": runs.get("ad-hoc", []),
            "total_specs": len(specs),
            "total_runs": sum(len(v) for v in runs.values()),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"📋 DevSwarm Backlog — {len(specs)} spec(s) on file")
    print()
    print(render_table(specs, runs))
    print()
    print(f"workspace runs: {sum(len(v) for v in runs.values())} total")
    if runs.get("ad-hoc"):
        print(f"ad-hoc / unmatched runs: {len(runs['ad-hoc'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
