#!/usr/bin/env python
"""Generate docs/STATUS.md — measured facts only.

Hand-typed counts drift: the repo's own docs once said 47, 274, 304 and 398 tests and
18, 25 and 33 tables at the same time. Everything here is computed from the checkout
(git, ORM metadata, the FastAPI app, migration files, grep), so it cannot disagree
with the code. Regenerate with ``make status-report`` before merging.

No network, no database connection, no secrets.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata as md
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "STATUS.md"

# Public entry point of each pure calc engine -> what it is for.
CALC_ENGINES = {
    "consume_bom": "BOM 扣料",
    "resolve_discounts": "折扣疊加",
    "detect_variance": "COGS 異常偵測",
    "classify_hours": "勞基法工時分桶",
    "validate_uniform_invoice": "統一編號檢核",
    "compute_daily_pnl": "每日真實損益",
}


def sh(*args: str, check: bool = True) -> str:
    done = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{done.stderr[-800:]}")
    return done.stdout


def tracked_files() -> list[str]:
    return [f for f in sh("git", "-c", "core.quotepath=false", "ls-files").splitlines() if f]


def loc(files: list[str]) -> int:
    total = 0
    for f in files:
        with contextlib.suppress(OSError):
            total += (ROOT / f).read_text(encoding="utf-8", errors="ignore").count("\n")
    return total


def version(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return "not installed"


def test_counts() -> tuple[int, int]:
    """(restaurant+devswarm, tigerline) collected test counts, via pytest --collect-only."""
    out = sh(
        sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
        "-p", "no:cacheprovider", check=False,
    )
    rest = tiger = 0
    for line in out.splitlines():
        m = re.match(r"^(tests/\S+\.py): (\d+)$", line.strip())
        if m:
            if m.group(1).startswith("tests/tigerline/"):
                tiger += int(m.group(2))
            else:
                rest += int(m.group(2))
    return rest, tiger


def api_facts() -> tuple[int, int, int, int, list[str]]:
    sys.path.insert(0, str(ROOT))
    from restaurant_api.main import app
    from restaurant_api.models import Base

    spec = app.openapi()
    ops = sum(1 for p in spec["paths"].values() for m in p if m in ("get", "post", "put", "patch", "delete"))
    groups = sorted({p.strip("/").split("/")[0] or "/" for p in spec["paths"]})
    return len(spec["paths"]), ops, len(spec.get("components", {}).get("schemas", {})), len(Base.metadata.tables), groups


def migrations() -> tuple[int, list[str]]:
    revs: dict[str, str | None] = {}
    for f in sorted((ROOT / "restaurant_api" / "alembic" / "versions").glob("*.py")):
        text = f.read_text(encoding="utf-8")
        rev = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([0-9a-f]+)['\"]", text, re.M)
        down = re.search(r"^down_revision(?::[^=]+)?\s*=\s*(?:['\"]([0-9a-f]+)['\"]|None)", text, re.M)
        if rev:
            revs[rev.group(1)] = down.group(1) if down else None
    parents = {d for d in revs.values() if d}
    return len(revs), sorted(set(revs) - parents)


def engine_wiring(files: list[str]) -> list[tuple[str, str, int]]:
    app_py = [
        f for f in files
        if f.endswith(".py")
        and f.startswith(("restaurant_api/", "scripts/"))
        and not f.startswith("restaurant_api/services/calc/")
        and not f.startswith("restaurant_api/alembic/")
    ]
    texts = {f: (ROOT / f).read_text(encoding="utf-8", errors="ignore") for f in app_py}
    rows = []
    for fn, label in CALC_ENGINES.items():
        pat = re.compile(rf"\b{fn}\b")
        rows.append((fn, label, sum(len(pat.findall(t)) for t in texts.values())))
    return rows


def build() -> str:
    files = tracked_files()
    top = Counter(f.split("/")[0] for f in files)
    py = [f for f in files if f.endswith(".py")]
    sha = sh("git", "rev-parse", "--short", "HEAD").strip()
    branch = sh("git", "branch", "--show-current").strip() or "(detached)"
    when = sh("git", "log", "-1", "--format=%cI").strip()
    rest_tests, tiger_tests = test_counts()
    paths, ops, schemas, tables, groups = api_facts()
    n_mig, heads = migrations()
    wiring = engine_wiring(files)

    L: list[str] = []
    a = L.append
    a("# STATUS — 實測事實（機器產生，勿手改）")
    a("")
    a(f"> 產生於 `{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}` · commit `{sha}` ({when}) · 分支 `{branch}`")
    a("> 重新產生：`make status-report`。這裡的每個數字都從 checkout 算出來（git / ORM / FastAPI app / migration 檔 / grep）。")
    a("> 其他文件若與這份衝突，**以這份為準**，並請把那份文件改成指向這裡，不要再手寫數字。")
    a("")
    a("## 規模")
    a("")
    a("| 項目 | 實測 |")
    a("|---|---|")
    a(f"| ORM 資料表 | **{tables}** |")
    a(f"| API paths / operations / schemas | **{paths}** / **{ops}** / **{schemas}** |")
    a(f"| API 群組 | {', '.join(f'`{g}`' for g in groups)} |")
    a(f"| Alembic migration 檔 | **{n_mig}**；head：{', '.join(f'`{h}`' for h in heads) or '—'}"
      + ("" if len(heads) == 1 else " ⚠️ **多個 head，`alembic upgrade head` 會失敗**") + " |")
    a(f"| 測試（餐飲 + DevSwarm，`make full-check`） | **{rest_tests}** 個 |")
    a(f"| 測試（TIGER LINE，`make tigerline-check`） | **{tiger_tests}** 個 |")
    a("")
    a("## 程式碼量（git 追蹤檔）")
    a("")
    a("| 目錄 | 檔案數 | Python 行數 |")
    a("|---|---:|---:|")
    for d in ("restaurant_api", "devswarm", "tigerline", "tests", "scripts", "sports-intelligence-desk", "tigerline-web"):
        in_d = [f for f in files if f.startswith(d + "/")]
        pyd = [f for f in in_d if f.endswith(".py")]
        a(f"| `{d}/` | {len(in_d)} | {loc(pyd):,} |")
    a(f"| 全部 | {len(files)} | {loc(py):,} |")
    a("")
    a("## 計算引擎有沒有真的接到系統（grep 應用程式碼，排除 `services/calc/` 與測試）")
    a("")
    a("| 引擎函式 | 用途 | 應用程式呼叫點 | 狀態 |")
    a("|---|---|---:|---|")
    for fn, label, n in wiring:
        a(f"| `{fn}` | {label} | {n} | {'✅ 已接線' if n else '❌ **沒有任何呼叫者（孤兒程式碼）**'} |")
    a("")
    a("## 工具版本（本次產生時）")
    a("")
    a("| 工具 | 版本 |")
    a("|---|---|")
    for pkg in ("ruff", "pyright", "SQLAlchemy", "FastAPI", "pydantic", "alembic"):
        a(f"| {pkg} | {version(pkg)} |")
    a(f"| Python | {sys.version.split()[0]} |")
    a("")
    a("> ruff / pyright 版本鎖在 `requirements-dev.txt`；執行期依賴只鎖大版號，所以 SQLAlchemy 等會隨時間浮動。")
    a("")
    a(f"_檔案頂層目錄分佈：{', '.join(f'{k} {v}' for k, v in top.most_common(8))}_")
    a("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stdout", action="store_true", help="print instead of writing docs/STATUS.md")
    args = ap.parse_args()
    text = build()
    if args.stdout:
        print(text)
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
