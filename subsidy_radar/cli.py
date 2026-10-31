"""Typer CLI — `python -m subsidy_radar` 或 `subsidy` console script。

子命令：
  scan      — 完整 pipeline（加 --llm 用 Claude 精煉配對）
  list      — 列出 seed 裡所有補助案
  match     — 只跑配對（不產行動包）
  apply     — 開始追蹤一案的申請（建立應備文件 checklist）
  doc       — 更新文件狀態（已備 / 未備 / 不適用）
  status    — 申請中案件的截止日與缺件
  draft     — Claude 代擬申請書草稿
  remind    — 截止日與缺件提醒（加 --send 推到 LINE）
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from restaurant_api.integrations.line import HttpLineMessenger, LineApiError
from subsidy_radar.draft import DraftError, draft_application, render_draft
from subsidy_radar.match import match_all
from subsidy_radar.models import (
    DocStatus,
    SubsidyApplication,
    SubsidyProgram,
    Verdict,
    llm_model,
    taipei_today,
)
from subsidy_radar.pipeline import (
    load_profile,
    load_programs_from_seed,
    run_offline,
)
from subsidy_radar.remind import (
    build_reminders,
    describe_deadline,
    push_reminders,
    render_reminder_text,
)
from subsidy_radar.tracker import (
    load_applications,
    missing_documents,
    progress,
    resolve_document,
    save_applications,
    set_document,
    start_application,
)

app = typer.Typer(help="🛰️ 政府補助雷達 — 抓→蒸餾→配對→行動包→申請")
console = Console()

_DEFAULT_SEED = Path("data/subsidies/seed_2026.json")
_DEFAULT_PROFILE = Path("data/subsidies/company_profile.yaml")
_DEFAULT_OUTPUT = Path("docs/subsidies/RADAR_2026.md")
_DEFAULT_TRACKER = Path("data/subsidies/applications.yaml")
_DEFAULT_DRAFT_DIR = Path("docs/subsidies/drafts")


_OPT_SEED = typer.Option(_DEFAULT_SEED, help="補助案 seed JSON")
_OPT_PROFILE = typer.Option(_DEFAULT_PROFILE, help="公司側寫 YAML")
_OPT_OUTPUT = typer.Option(_DEFAULT_OUTPUT, help="輸出報告路徑")
_OPT_TRACKER = typer.Option(_DEFAULT_TRACKER, help="申請追蹤 YAML")
_OPT_LLM = typer.Option(
    False, "--llm", help="用 Claude 精煉可行動案件（需 ANTHROPIC_API_KEY，會產生 API 費用）"
)


@app.command()
def scan(
    seed: Path = _OPT_SEED,
    profile: Path = _OPT_PROFILE,
    output: Path = _OPT_OUTPUT,
    tracker: Path = _OPT_TRACKER,
    llm: bool = _OPT_LLM,
) -> None:
    """跑完整 pipeline → 輸出 Markdown 雷達報告。"""
    console.print("[bold cyan]🛰️ 補助雷達啟動[/]")
    console.print(f"  補助 seed ：{seed}")
    console.print(f"  公司側寫  ：{profile}")
    console.print(f"  輸出報告  ：{output}")
    console.print(f"  AI 精煉   ：{'開' if llm else '關'}")
    console.print()

    report = run_offline(seed, profile, output, llm=llm, tracker_path=tracker)

    eligible = [m for m in report.matches if m.verdict in {Verdict.eligible, Verdict.likely}]
    actionable = [m for m in report.matches if m.verdict in {Verdict.eligible, Verdict.likely, Verdict.conditional}]

    console.print(f"[green]掃描完成[/]：{len(report.programs)} 案 → {len(actionable)} 可行動 → {len(eligible)} 高度符合")
    if llm:
        refined = sum(1 for m in actionable if m.strategic_notes)
        console.print(f"AI 精煉：{refined}/{len(actionable)} 案完成（其餘保留規則判定）")
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


# ── 申請追蹤 ──────────────────────────────────────────────────────────────

_DOC_MARK = {DocStatus.done: "✅", DocStatus.todo: "⬜", DocStatus.na: "➖"}
_ARG_DOC_STATUS = typer.Argument(DocStatus.done, help="todo（未備）/ done（已備）/ na（不適用）")


def _find_program(seed: Path, program_id: str) -> SubsidyProgram:
    for program in load_programs_from_seed(seed):
        if program.id == program_id:
            return program
    console.print(f"[red]找不到補助案 {program_id}[/]，用 `list` 查 ID")
    raise typer.Exit(1)


def _print_checklist(application: SubsidyApplication) -> None:
    if not application.documents:
        console.print("  （沒有應備文件清單，可用 doc 新增）")
        return
    for i, (document, status) in enumerate(application.documents.items(), 1):
        console.print(f"  {i}. {_DOC_MARK[status]} {document}", markup=False)
    done, needed = progress(application)
    console.print(f"  文件進度：{done}/{needed}")


@app.command()
def apply(
    program_id: str,
    seed: Path = _OPT_SEED,
    tracker: Path = _OPT_TRACKER,
) -> None:
    """開始追蹤一案的申請：依應備文件建立 checklist。"""
    program = _find_program(seed, program_id)
    applications = load_applications(tracker)
    if program_id in applications:
        console.print(f"{program.name} 已在追蹤中")
    else:
        applications[program_id] = start_application(program)
        save_applications(tracker, applications)
        console.print(f"[green]開始追蹤[/]：{program.name}")
    _print_checklist(applications[program_id])


@app.command()
def doc(
    program_id: str,
    document: str,
    status: DocStatus = _ARG_DOC_STATUS,
    tracker: Path = _OPT_TRACKER,
) -> None:
    """更新文件狀態；document 填 apply/status 列出的編號，或文件全名（查無則新增）。"""
    applications = load_applications(tracker)
    application = applications.get(program_id)
    if application is None:
        console.print(f"[red]{program_id} 還沒開始追蹤[/]，先執行 apply {program_id}")
        raise typer.Exit(1)
    name = resolve_document(application, document)
    verb = "更新" if name in application.documents else "新增"
    applications[program_id] = set_document(application, name, status)
    save_applications(tracker, applications)
    console.print(f"{verb}：{name} → {status.value}", markup=False)
    _print_checklist(applications[program_id])


@app.command("status")
def show_status(
    seed: Path = _OPT_SEED,
    tracker: Path = _OPT_TRACKER,
) -> None:
    """列出申請中的案件：截止日、文件進度、還缺什麼。"""
    applications = load_applications(tracker)
    if not applications:
        console.print("還沒有追蹤中的申請，用 `apply <program_id>` 開始。")
        return
    programs = {p.id: p for p in load_programs_from_seed(seed)}
    today = taipei_today()
    table = Table(title=f"申請進度（{today}）")
    table.add_column("補助案")
    table.add_column("截止")
    table.add_column("文件")
    table.add_column("還缺")
    for program_id, application in applications.items():
        program = programs.get(program_id)
        done, needed = progress(application)
        table.add_row(
            program.name if program else program_id,
            describe_deadline(program.window_close if program else None, today),
            f"{done}/{needed}",
            "、".join(missing_documents(application)) or "—",
        )
    console.print(table)


# ── 代擬與提醒 ────────────────────────────────────────────────────────────

_OPT_DRAFT_DIR = typer.Option(_DEFAULT_DRAFT_DIR, help="草稿輸出資料夾")
_OPT_DAYS = typer.Option(14, help="幾天內截止的案件要提醒")
_OPT_SEND = typer.Option(False, "--send", help="推送到 LINE（需 LINE_CHANNEL_ACCESS_TOKEN）")
_OPT_TO = typer.Option("", "--to", envvar="SUBSIDY_LINE_TO", help="接收提醒的 LINE user ID")


@app.command()
def draft(
    program_id: str,
    seed: Path = _OPT_SEED,
    profile: Path = _OPT_PROFILE,
    out_dir: Path = _OPT_DRAFT_DIR,
) -> None:
    """Claude 代擬申請書草稿（需 ANTHROPIC_API_KEY，會產生 API 費用）。"""
    program = _find_program(seed, program_id)
    company = load_profile(profile)
    model = llm_model()
    console.print(f"代擬中：{program.name}（{model}，約需一兩分鐘）")
    try:
        body = asyncio.run(draft_application(program, company, anthropic.AsyncAnthropic()))
    except TypeError as exc:
        if "Could not resolve authentication method" not in str(exc):
            raise
        console.print("[red]找不到 Anthropic API 憑證[/]：請設定 ANTHROPIC_API_KEY")
        raise typer.Exit(1) from exc
    except DraftError as exc:
        console.print(f"[red]草稿產生失敗[/]：{exc}")
        raise typer.Exit(1) from exc
    path = out_dir / f"{program_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_draft(program, body, model, datetime.now(UTC)), encoding="utf-8")
    console.print(f"[green]草稿已輸出[/]：{path}（送件前請逐字覆核）")


async def _push_and_close(messenger: HttpLineMessenger, to: str, text: str) -> None:
    try:
        await push_reminders(messenger, to, text)
    finally:
        await messenger.aclose()


@app.command()
def remind(
    seed: Path = _OPT_SEED,
    profile: Path = _OPT_PROFILE,
    tracker: Path = _OPT_TRACKER,
    days: int = _OPT_DAYS,
    send: bool = _OPT_SEND,
    to: str = _OPT_TO,
) -> None:
    """截止日與缺件提醒；預設只印出，加 --send 推到 LINE。"""
    today = taipei_today()
    programs = load_programs_from_seed(seed)
    matches = match_all(programs, load_profile(profile), today=today)
    reminders = build_reminders(programs, matches, load_applications(tracker), today, days)
    if not reminders:
        console.print("目前沒有需要提醒的事項")
        return
    text = render_reminder_text(reminders, today)
    console.print(text, markup=False, highlight=False)
    if not send:
        return
    if not to:
        console.print("[red]需要接收者[/]：加 --to 或設定 SUBSIDY_LINE_TO")
        raise typer.Exit(1)
    try:
        messenger = HttpLineMessenger.from_env()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    try:
        asyncio.run(_push_and_close(messenger, to, text))
    except LineApiError as exc:
        console.print(f"[red]LINE 推送失敗[/]：{escape(str(exc))}")
        raise typer.Exit(1) from exc
    console.print("[green]已推送到 LINE[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
