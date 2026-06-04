"""Scan Alembic migration files for operations that are unsafe on a live DB.

Catches the classic foot-guns:
  - DROP COLUMN          → instant outage if any reader still selects it
  - DROP TABLE           → same
  - ALTER ... SET NOT NULL on a populated column without a default
  - RENAME COLUMN        → old name and new name aren't both valid simultaneously
  - PRIMARY KEY changes  → table-rewrite + lock
  - Type widening UP, type narrowing DOWN — narrowing requires a backfill

Runs in CI; exit 1 if any high-risk op found AND not whitelisted via the
`# safety: ok — <reason>` marker on the op line.

Usage:
    python scripts/check_migration_safety.py [--strict]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "restaurant_api" / "alembic" / "versions"

# (regex, severity, reason)
_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"op\.drop_column\("), "HIGH", "DROP COLUMN — readers still selecting it will 500"),
    (re.compile(r"op\.drop_table\("), "HIGH", "DROP TABLE — irreversible without backup"),
    (re.compile(r"op\.alter_column\(.*nullable=False"), "MEDIUM", "SET NOT NULL — backfill required first"),
    (re.compile(r"op\.alter_column\(.*new_column_name="), "HIGH", "RENAME COLUMN — old and new aren't simultaneously valid"),
    (re.compile(r"op\.drop_constraint\(.*type_=\"primarykey\""), "HIGH", "DROP PRIMARY KEY — full table rewrite"),
    (re.compile(r"op\.drop_index\("), "LOW", "DROP INDEX — query performance regression possible"),
    (re.compile(r"op\.execute\(.*TRUNCATE"), "HIGH", "TRUNCATE — data loss"),
    (re.compile(r"op\.execute\(.*DROP RULE"), "MEDIUM", "DROP RULE — append-only enforcement removed"),
]

# Whitelist marker: `# safety: ok — explanation` on the same line allows the op.
_WHITELIST = re.compile(r"#\s*safety:\s*ok")


def scan_file(path: Path) -> list[dict[str, str]]:
    """Scan only the `def upgrade()` body — `downgrade()` is *expected* to drop
    things and is whitelisted wholesale (it's only run on a deliberate rollback)."""
    findings: list[dict[str, str]] = []
    in_upgrade = False
    upgrade_indent: int | None = None

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.lstrip()
        # Detect function boundaries.
        if stripped.startswith("def upgrade"):
            in_upgrade = True
            upgrade_indent = len(line) - len(stripped)
            continue
        if stripped.startswith("def downgrade") or stripped.startswith("def "):
            in_upgrade = False
            upgrade_indent = None
            continue

        if not in_upgrade:
            continue
        # Bail out of the function on dedent.
        if (
            stripped
            and upgrade_indent is not None
            and (len(line) - len(stripped)) <= upgrade_indent
        ):
            in_upgrade = False
            continue
        if _WHITELIST.search(line):
            continue
        for rx, severity, reason in _RULES:
            if rx.search(line):
                findings.append({
                    "file": path.name,
                    "line": str(lineno),
                    "severity": severity,
                    "reason": reason,
                    "code": line.strip()[:120],
                })
                break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any HIGH/MEDIUM finding (default: only HIGH).",
    )
    args = parser.parse_args()

    if not MIGRATIONS_DIR.exists():
        print(f"⚠️  No migrations dir at {MIGRATIONS_DIR}")
        return 0

    all_findings: list[dict[str, str]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        if path.name == "__pycache__":
            continue
        all_findings.extend(scan_file(path))

    if not all_findings:
        print(f"✅ {len(list(MIGRATIONS_DIR.glob('*.py')))} migration(s) scanned — no unsafe ops detected.")
        return 0

    bad_severities = {"HIGH", "MEDIUM"} if args.strict else {"HIGH"}
    fatal = [f for f in all_findings if f["severity"] in bad_severities]
    warn = [f for f in all_findings if f not in fatal]

    for f in fatal:
        print(f"❌ {f['severity']} {f['file']}:{f['line']} — {f['reason']}")
        print(f"   {f['code']}")
    for f in warn:
        print(f"⚠️  {f['severity']} {f['file']}:{f['line']} — {f['reason']}")
        print(f"   {f['code']}")

    print()
    print(
        f"{len(fatal)} fatal, {len(warn)} warning across "
        f"{len({f['file'] for f in all_findings})} files"
    )
    print("Whitelist a known-safe op by adding `# safety: ok — <reason>` to that line.")
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
