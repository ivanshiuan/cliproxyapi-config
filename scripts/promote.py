"""Promote a DevSwarm task's artifacts from workspace/<task_id>/ into the
restaurant_api package, with safety gates.

What it does (in order):
  1. Locate workspace/<task_id>/ and read task.json
  2. Find the module + test files (single .py module + single test_*.py)
  3. Validate the test file passes pytest in the workspace
  4. Copy the module to restaurant_api/services/<module>.py
  5. Copy the test to tests/services/test_<module>.py with import rewritten
     so it imports `from restaurant_api.services.<module> import ...`
  6. Re-run pytest on the copied test to confirm portability
  7. Stage the new files (git add) and print a suggested commit command

Bails (no copy) if any step fails. Idempotent: re-running with the same
task_id overwrites the same destinations.

Usage:
    python scripts/promote.py <task_id>
    python scripts/promote.py abc12345 --dest restaurant_api/calc/
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = REPO_ROOT / "workspace"
DEFAULT_MODULE_DEST = REPO_ROOT / "restaurant_api" / "services"
DEFAULT_TEST_DEST = REPO_ROOT / "tests" / "services"


def _find_workspace(task_id: str) -> Path:
    path = WORKSPACE_ROOT / task_id
    if not path.is_dir():
        sys.exit(f"❌ workspace/{task_id}/ not found")
    if not (path / "task.json").exists():
        sys.exit(f"❌ workspace/{task_id}/task.json missing — not a DevSwarm task dir")
    return path


def _find_artifacts(ws: Path) -> tuple[Path, Path]:
    """Find the module file and the test file. Expects exactly one of each."""
    pys = sorted(p for p in ws.iterdir() if p.suffix == ".py")
    tests = [p for p in pys if p.name.startswith("test_")]
    modules = [p for p in pys if not p.name.startswith("test_")]
    if len(modules) != 1:
        sys.exit(f"❌ expected exactly 1 module .py in {ws}, found {len(modules)}: {modules}")
    if len(tests) != 1:
        sys.exit(f"❌ expected exactly 1 test_*.py in {ws}, found {len(tests)}: {tests}")
    return modules[0], tests[0]


def _run_pytest(cwd: Path) -> bool:
    """Run pytest in `cwd`; return True iff exit code 0."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "--tb=short", "-q"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        print("--- pytest stdout (tail) ---")
        print(result.stdout[-2000:])
        print("--- pytest stderr (tail) ---")
        print(result.stderr[-1000:])
    return result.returncode == 0


def _rewrite_test_imports(test_src: str, module_name: str, dest_pkg: str) -> str:
    """Rewrite ``from <module_name> import ...`` to import from the dest package.

    Conservative: only rewrites bare imports of the module by exact name.
    """
    patterns = [
        (rf"^from\s+{re.escape(module_name)}\s+import\b", f"from {dest_pkg}.{module_name} import"),
        (rf"^import\s+{re.escape(module_name)}\b", f"import {dest_pkg}.{module_name} as {module_name}"),
    ]
    out_lines: list[str] = []
    for line in test_src.splitlines(keepends=True):
        new_line = line
        for pat, repl in patterns:
            new_line = re.sub(pat, repl, new_line, flags=re.MULTILINE)
        out_lines.append(new_line)
    return "".join(out_lines)


def _ensure_pkg(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    init = path / "__init__.py"
    if not init.exists():
        init.write_text('"""Auto-created by scripts/promote.py."""\n', encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="The 8-char DevSwarm task id (workspace/<task_id>/)")
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_MODULE_DEST,
        help="Module destination directory (default: restaurant_api/services/)",
    )
    parser.add_argument(
        "--test-dest",
        type=Path,
        default=DEFAULT_TEST_DEST,
        help="Test destination directory (default: tests/services/)",
    )
    parser.add_argument(
        "--no-stage",
        action="store_true",
        help="Skip `git add` after copying (default: stage the files)",
    )
    args = parser.parse_args()

    ws = _find_workspace(args.task_id)
    module_src, test_src = _find_artifacts(ws)
    module_name = module_src.stem

    print(f"📦 task {args.task_id}: promoting {module_name}.py + {test_src.name}")

    # 1. Pytest must pass in the workspace before we promote.
    print("  · running workspace pytest (gate 1)...")
    if not _run_pytest(ws):
        sys.exit("❌ workspace tests failed — refusing to promote")
    print("    ✅ pass")

    # 2. Compute destination paths.
    dest_module_dir = args.dest.resolve()
    dest_test_dir = args.test_dest.resolve()
    _ensure_pkg(dest_module_dir)
    _ensure_pkg(dest_test_dir)

    dest_module = dest_module_dir / f"{module_name}.py"
    dest_test = dest_test_dir / f"test_{module_name}.py"

    # 3. Copy the module verbatim.
    shutil.copy2(module_src, dest_module)
    print(f"  · copied module → {dest_module.relative_to(REPO_ROOT)}")

    # 4. Rewrite test imports for the new location.
    rel_dest_pkg = ".".join(dest_module_dir.relative_to(REPO_ROOT).parts)
    test_body = test_src.read_text(encoding="utf-8")
    new_test_body = _rewrite_test_imports(test_body, module_name, rel_dest_pkg)
    dest_test.write_text(new_test_body, encoding="utf-8")
    print(f"  · copied test (with rewritten imports) → {dest_test.relative_to(REPO_ROOT)}")

    # 5. Run the test in the new location (gate 2).
    print("  · re-running pytest at destination (gate 2)...")
    test_result = subprocess.run(
        [sys.executable, "-m", "pytest", str(dest_test), "-x", "--tb=short", "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if test_result.returncode != 0:
        print("--- pytest stdout (tail) ---")
        print(test_result.stdout[-2000:])
        # Roll back the copy so the repo isn't left in a broken state.
        dest_module.unlink(missing_ok=True)
        dest_test.unlink(missing_ok=True)
        sys.exit("❌ destination test FAILED — rolled back, please inspect manually")
    print("    ✅ pass")

    # 6. Git stage if requested.
    if not args.no_stage:
        subprocess.run(
            ["git", "add", str(dest_module), str(dest_test)],
            cwd=str(REPO_ROOT),
            check=False,
        )
        print(f"  · staged: {dest_module.relative_to(REPO_ROOT)}, "
              f"{dest_test.relative_to(REPO_ROOT)}")

    # 7. Suggest the commit.
    print()
    print(f"✅ promoted {module_name} from task {args.task_id}")
    print()
    print("Suggested commit:")
    print(f"  git commit -m 'feat(services): import {module_name} from DevSwarm "
          f"task {args.task_id}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
