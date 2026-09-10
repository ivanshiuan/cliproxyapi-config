"""Validate a DevSwarm spec file against the structured-spec contract.

Run before `make swarm` to catch underspec specs that would burn Coder budget.

Usage:
    python scripts/validate_spec.py specs/profit_calc.md      # one file
    python scripts/validate_spec.py specs/*.md                # many
    python scripts/validate_spec.py --all                     # everything in specs/
    python scripts/validate_spec.py --json specs/foo.md       # machine-readable

Exit codes:
    0  all specs valid
    1  one or more specs failed validation
    2  bad arguments / file not found

The contract (stolen and adapted from openai/gpt-5-coding-examples' YAML-spec
pattern, but tuned for DevSwarm's restaurant-domain pure-function modules):

  Frontmatter (YAML between leading `---` fences) MUST contain:
    id, title, module, status, preferred_model, budget_usd, tags, ac_count

  Body MUST have these H2 sections (EN or ZH names accepted):
    Background  | 背景
    Goal        | 目標
    Scope       | 範圍              (with In/Out subsections)
    Acceptance Criteria             (with ≥10 "AC-" markers)
    Constraints | 限制

  Body SHOULD be 150-350 lines (the spec-writer agent targets 180-280).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"

VALID_STATUSES = {"draft", "ready", "implemented", "deprecated"}
VALID_MODELS = {"opus", "sonnet", "haiku"}
VALID_KINDS = {"pure-function", "router"}
REQUIRED_KEYS = {
    "id",
    "title",
    "module",
    "kind",
    "status",
    "preferred_model",
    "budget_usd",
    "tags",
    "ac_count",
}

# Section aliases — each entry lists prefixes a normalized H2 heading may start with.
# Numeric `1.` / `1.1.` prefixes are stripped before matching.
_BACKGROUND = ["background", "背景"]
_GOAL = ["goal", "目標"]
_SCOPE = ["scope", "範圍"]
_PUBLIC_IF = ["public interface", "public api", "公開介面"]
_ACS = ["acceptance criteria", "acceptance criterion", "驗收條件", "驗收標準"]
_CONSTRAINTS = ["constraints", "constraint", "硬性約束", "限制"]
_ROUTES = ["routes", "endpoints", "routing", "路由"]
_SCHEMAS = ["pydantic schemas", "schemas", "pydantic 模型", "request/response", "模型"]
_ERRORS = ["error responses", "errors", "錯誤回應", "錯誤碼"]

REQUIRED_SECTIONS = {
    "pure-function": {
        "background": _BACKGROUND,
        "goal": _GOAL,
        "scope": _SCOPE,
        "public_interface": _PUBLIC_IF,
        "acceptance_criteria": _ACS,
        "constraints": _CONSTRAINTS,
    },
    "router": {
        "background": _BACKGROUND,
        "routes": _ROUTES,
        "schemas": _SCHEMAS,
        "acceptance_criteria": _ACS,
        "errors": _ERRORS,
    },
}

# Strip leading `N.` / `N.M.` / `第 N 章` prefixes before alias match.
_HEADING_PREFIX_RE = re.compile(r"^(\d+(\.\d+)*\.?\s+|第\s*\d+\s*[章節]\s*)")

MIN_ACS = 10
MIN_LINES = 100
MAX_LINES = 400
SOFT_MIN_LINES = 150  # warn under this
SOFT_MAX_LINES = 280  # warn over this


# ──────────────────────────────────────────────────────────────────────────
# Mini YAML parser — frontmatter only, supports flat key:value and inline lists
# ──────────────────────────────────────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a markdown doc into (frontmatter_dict, body).

    Returns (None, full_text) if no frontmatter fence is present.
    Raises ValueError on malformed YAML.
    """
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None, text
    # Find the closing fence on its own line.
    lines = text.splitlines(keepends=True)
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("frontmatter opens with `---` but never closes")
    raw = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    return _parse_yaml_flat(raw), body


def _parse_yaml_flat(raw: str) -> dict[str, Any]:
    """Parse a flat YAML subset: `key: scalar` and `key: [a, b, c]`.

    No nesting, no block lists, no anchors. Intentionally tiny.
    """
    out: dict[str, Any] = {}
    for line_no, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise ValueError(f"line {line_no}: expected `key: value`, got {stripped!r}")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        out[key] = _coerce_scalar(value)
    return out


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _coerce_scalar(value: str) -> Any:
    if value == "":
        return ""
    if value.lower() in {"null", "~"}:
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(item.strip()) for item in inner.split(",")]
    if _NUM_RE.match(value):
        return float(value) if "." in value else int(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


# ──────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class SpecReport:
    path: Path
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] | None = None
    body_lines: int = 0
    ac_count: int = 0
    sections_found: dict[str, str] = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def validate(path: Path) -> SpecReport:
    report = SpecReport(path=path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        report.fail(f"unreadable: {e}")
        return report

    try:
        fm, body = _parse_frontmatter(text)
    except ValueError as e:
        report.fail(f"frontmatter parse error: {e}")
        return report

    if fm is None:
        report.fail(
            "missing YAML frontmatter. Add a `---`-fenced block at the top with "
            "fields: " + ", ".join(sorted(REQUIRED_KEYS))
        )
        return report

    report.frontmatter = fm
    _check_frontmatter(fm, path, report)

    body_lines = body.count("\n") + (0 if body.endswith("\n") else 1)
    report.body_lines = body_lines
    if body_lines < MIN_LINES:
        report.fail(f"body has {body_lines} lines, minimum {MIN_LINES} — spec too thin for Coder")
    elif body_lines > MAX_LINES:
        report.fail(f"body has {body_lines} lines, maximum {MAX_LINES} — spec over-scoped")
    else:
        if body_lines < SOFT_MIN_LINES:
            report.warn(f"body has {body_lines} lines, target ≥{SOFT_MIN_LINES}")
        if body_lines > SOFT_MAX_LINES:
            report.warn(f"body has {body_lines} lines, target ≤{SOFT_MAX_LINES}")

    _check_sections(body, fm, report)
    _check_acs(body, report, fm)
    _check_no_float(body, report)

    return report


def _check_frontmatter(fm: dict[str, Any], path: Path, report: SpecReport) -> None:
    missing = REQUIRED_KEYS - fm.keys()
    if missing:
        report.fail(f"frontmatter missing keys: {sorted(missing)}")

    if "id" in fm and fm["id"] != path.stem:
        report.fail(f"frontmatter id={fm['id']!r} must match filename stem {path.stem!r}")

    status = fm.get("status")
    if status is not None and status not in VALID_STATUSES:
        report.fail(f"status={status!r} not in {sorted(VALID_STATUSES)}")

    pm = fm.get("preferred_model")
    if pm is not None and pm not in VALID_MODELS:
        report.fail(f"preferred_model={pm!r} not in {sorted(VALID_MODELS)}")

    kind = fm.get("kind")
    if kind is not None and kind not in VALID_KINDS:
        report.fail(f"kind={kind!r} not in {sorted(VALID_KINDS)}")

    budget = fm.get("budget_usd")
    if budget is not None and (not isinstance(budget, (int, float)) or budget <= 0):
        report.fail(f"budget_usd must be positive number, got {budget!r}")

    tags = fm.get("tags")
    if tags is not None and not isinstance(tags, list):
        report.fail(f"tags must be a list, got {type(tags).__name__}: {tags!r}")

    module = fm.get("module")
    if module is not None and not re.match(r"^[a-z][a-z0-9_]*$", str(module)):
        report.fail(f"module={module!r} must be snake_case ascii")


def _normalize_heading(raw: str) -> str:
    h = raw.strip().lower()
    h = _HEADING_PREFIX_RE.sub("", h)
    return h.strip()


def _check_sections(body: str, fm: dict[str, Any], report: SpecReport) -> None:
    raw_headings = re.findall(r"^##\s+(.+?)\s*$", body, re.M)
    headings = [_normalize_heading(h) for h in raw_headings]
    kind = fm.get("kind") or "pure-function"
    required = REQUIRED_SECTIONS.get(kind, REQUIRED_SECTIONS["pure-function"])

    for key, aliases in required.items():
        found = next((h for h in headings if any(h.startswith(a) for a in aliases)), None)
        if found is None:
            report.fail(
                f"missing required section for kind={kind!r}: one of {aliases} (as H2 heading)"
            )
        else:
            report.sections_found[key] = found

    if "scope" in report.sections_found and not re.search(
        r"out[- ]?of[- ]?scope|範圍外|不在範圍", body, re.I
    ):
        report.warn("Scope section should include an 'Out of scope' subsection")


def _check_acs(body: str, report: SpecReport, fm: dict[str, Any]) -> None:
    ac_markers = re.findall(r"\bAC-?\d+\b", body)
    unique_acs = {m.upper().replace("AC", "AC-").replace("--", "-") for m in ac_markers}
    count = len(unique_acs)
    report.ac_count = count
    if count < MIN_ACS:
        report.fail(f"found {count} unique AC markers, minimum {MIN_ACS} (e.g. AC-1, AC-2 …)")

    declared = fm.get("ac_count")
    if isinstance(declared, int) and declared != count:
        report.warn(
            f"frontmatter ac_count={declared} but body has {count} — run "
            "`python scripts/validate_spec.py --fix-counts <file>` to update"
        )


def _check_no_float(body: str, report: SpecReport) -> None:
    # Heuristic: look for `float(` or `: float` in fenced python blocks.
    in_block = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_block = "python" in line.lower() if line.startswith("```") and not in_block else False
            continue
        if in_block and re.search(r"\bfloat\s*[\(\:]|=\s*float\b", line):
            report.warn(f"code block contains `float` usage — money/qty must be Decimal: {line.strip()!r}")
            break


# ──────────────────────────────────────────────────────────────────────────
# Auto-fix the ac_count field in-place
# ──────────────────────────────────────────────────────────────────────────


def fix_ac_counts(path: Path) -> bool:
    """Re-count ACs in body and update frontmatter ac_count. Returns True if changed."""
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    if fm is None:
        return False
    ac_markers = re.findall(r"\bAC-?\d+\b", body)
    unique_acs = {m.upper().replace("AC", "AC-").replace("--", "-") for m in ac_markers}
    new_count = len(unique_acs)
    if fm.get("ac_count") == new_count:
        return False
    # Rewrite frontmatter, preserving original order + non-data lines.
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            break
        if line.lstrip().startswith("ac_count:"):
            lines[i] = re.sub(r"ac_count:\s*\S+", f"ac_count: {new_count}", line)
            path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────


def _render(report: SpecReport) -> str:
    icon = "✅" if report.ok and not report.warnings else ("⚠️ " if report.ok else "❌")
    rel = report.path.relative_to(REPO_ROOT) if report.path.is_absolute() else report.path
    out = [f"{icon} {rel}  (body={report.body_lines}ln, ACs={report.ac_count})"]
    for e in report.errors:
        out.append(f"   ❌ {e}")
    for w in report.warnings:
        out.append(f"   ⚠️  {w}")
    return "\n".join(out)


def _to_dict(report: SpecReport) -> dict[str, Any]:
    return {
        "path": str(report.path.relative_to(REPO_ROOT) if report.path.is_absolute() else report.path),
        "ok": report.ok,
        "errors": report.errors,
        "warnings": report.warnings,
        "body_lines": report.body_lines,
        "ac_count": report.ac_count,
        "frontmatter": report.frontmatter,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", type=Path, help="spec .md files to validate")
    p.add_argument("--all", action="store_true", help="validate every specs/*.md")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    p.add_argument("--fix-counts", action="store_true", help="update frontmatter ac_count in-place")
    args = p.parse_args(argv)

    if args.all:
        paths = sorted(SPECS_DIR.glob("*.md"))
    else:
        paths = list(args.paths)

    if not paths:
        print("usage: validate_spec.py [--all] <file.md> [<file.md> ...]", file=sys.stderr)
        return 2

    if args.fix_counts:
        changed = [p for p in paths if p.exists() and fix_ac_counts(p)]
        for p in changed:
            print(f"updated ac_count in {p.relative_to(REPO_ROOT) if p.is_absolute() else p}")
        if not changed:
            print("no changes needed")
        return 0

    reports = [validate(p) for p in paths]
    if args.json:
        print(json.dumps([_to_dict(r) for r in reports], indent=2, ensure_ascii=False))
    else:
        for r in reports:
            print(_render(r))
        failed = sum(1 for r in reports if not r.ok)
        warned = sum(1 for r in reports if r.ok and r.warnings)
        print()
        print(f"summary: {len(reports) - failed} ok, {failed} failed, {warned} with warnings")

    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
