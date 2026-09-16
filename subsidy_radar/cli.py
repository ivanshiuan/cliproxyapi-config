"""Typer CLI — `python -m subsidy_radar` 或 `subsidy` console script。

子命令：
  scan      — 完整 pipeline（offline 或 online）
  list      — 列出 seed 裡所有補助案
  match     — 只跑配對（不產行動包）
  report    — 從上次 scan 結果重新渲染報告
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from subsidy_radar.pipeline import (
    load_profile,
    load_programs_from_seed,
    run_offline,
)

app = typer.Typer(help="🛰️ 政府補助雷達 — 抓→蒸餾→配對→行動包")
console = Console()

_DEFAULT_SEED = Path("data/subsidies/seed_2026.json")
_DEFAULT_PROFILE = Path("data/subsidies/company_profile.yaml")
_DEFAULT_OUTPUT = Path("docs/subsidies/RADAR_2026.md")


_OPT_SEED = typer.Option(_DEFAULT_SEED, help="補助案 seed JSON")
_OPT_PROFILE = typer.Option(_DEFAULT_PROFILE, help="公司側寫 YAML")
_OPT_OUTPUT = typer.Option(_DEFAULT_OUTPUT, help="輸出報告路徑")


@app.command()
def scan(
    seed: Path = _OPT_SEED,
    profile: Path = _OPT_PROFILE,
    output: Path = _OPT_OUTPUT,
) -> None:
    """跑完整 pipeline → 輸出 Markdown 雷達報告。"""
    console.print("[bold cyan]🛰️ 補助雷達啟動[/]")
    console.print(f"  補助 seed ：{seed}")
    console.print(f"  公司側寫  ：{profile}")
    console.print(f"  輸出報告  ：{output}")
    console.print()

    report = run_offline(seed, profile, output)

    from subsidy_radar.models import Verdict

    eligible = [m for m in report.matches if m.verdict in {Verdict.eligible, Verdict.likely}]
    actionable = [m for m in report.matches if m.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}]

    console.print(f"[green]掃描完成[/]：{len(report.programs)} 案 → {len(actionable)} 可行動 → {len(eligible)} 高度符合")
    if eligible:
        total = sum(m.estimated_value_twd for m in eligible if m.estimated_value_twd)
        console.print(f"[bold green]預估可爭取：NT$ {total:,.0f}[/]")
    console.print(f"\n報告已輸出：{output}")


_OPT_SEED_LIST = typer.Option(_DEFAULT_SEED, help="補助案 seed JSON")


@app.command("list")
def list_programs(
    seed: Path = _OPT_SEED_LIST,
) -> None:
    """列出 seed 裡所有補助案。"""
    programs = load_programs_from_seed(seed)
    table = Table(title="2026 補助案清單")
    table.add_column("#", style="dim")
    table.add_column("ID")
    table.add_column("名稱")
    table.add_column("主辦機關")
    table.add_column("類型")
    table.add_column("狀態")
    table.add_column("最高金額")

    for i, p in enumerate(programs, 1):
        amt = f"{p.max_amount_twd:,}" if p.max_amount_twd else "-"
        table.add_row(str(i), p.id, p.name, p.agency, p.category.value, p.status.value, amt)

    console.print(table)


_OPT_SEED_MATCH = typer.Option(_DEFAULT_SEED, help="補助案 seed JSON")
_OPT_PROFILE_MATCH = typer.Option(_DEFAULT_PROFILE, help="公司側寫 YAML")


@app.command()
def match(
    seed: Path = _OPT_SEED_MATCH,
    profile: Path = _OPT_PROFILE_MATCH,
) -> None:
    """只跑配對，輸出結果到終端。"""
    from subsidy_radar.match import match_all

    programs = load_programs_from_seed(seed)
    company = load_profile(profile)
    results = match_all(programs, company)

    table = Table(title="配對結果")
    table.add_column("補助案")
    table.add_column("判定")
    table.add_column("分數")
    table.add_column("說明")

    for m in results:
        table.add_row(m.program_name, m.verdict.value, str(m.score), m.reasoning)

    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
