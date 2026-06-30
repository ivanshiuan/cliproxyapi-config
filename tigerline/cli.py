"""TIGER LINE PRIME CLI — Typer app.

Subcommands:
- analyze <match.json>        — run the pipeline, save match + classification + plan
- review  <match_id> H-A      — settle a match, save result + suggestions
- backlog                     — list matches with a plan but no result
- stats                       — aggregate review hit-rates by scenario
- rules show / rules validate — inspect / sanity-check the YAML
- version                     — print version
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tigerline import __version__
from tigerline.calibration_engine import calibrate
from tigerline.classifier import classify
from tigerline.clv_tracker import clv_summary
from tigerline.consensus_engine import compute_consensus
from tigerline.harness import evaluate as evaluate_harness
from tigerline.line_movement_analyzer import analyze_movement
from tigerline.odds_snapshot_collector import (
    SnapshotRejected,
    record_snapshot,
)
from tigerline.odds_snapshot_collector import (
    load_snapshot as parse_snapshot_file,
)
from tigerline.parser import load_match as parse_match_file
from tigerline.precision_score_engine import score_precision
from tigerline.recommender import recommend
from tigerline.report_generator import (
    ReportContext,
    avoid_list_report,
    daily_pre_match_report,
    market_alert_report,
    post_match_review_report,
    weekly_calibration_report,
)
from tigerline.review import review as run_review
from tigerline.rules import (
    load_classification_rules,
    load_corridor_templates,
    load_stake_bands,
    reset_cache,
)
from tigerline.storage import (
    connect,
    latest_plan,
    latest_snapshot,
    list_backlog,
    list_snapshots,
    load_match,
    save_classification,
    save_match,
    save_recommendation,
    save_review,
    stats_summary,
)

app = typer.Typer(
    name="tiger",
    help="TIGER LINE PRIME — Asian-handicap-first match decision engine.",
    no_args_is_help=True,
    add_completion=False,
)
rules_app = typer.Typer(help="Inspect and validate YAML rule files.")
app.add_typer(rules_app, name="rules")

snapshot_app = typer.Typer(help="V3.0 odds-snapshot collector (Sprint 1).")
app.add_typer(snapshot_app, name="snapshot")

movement_app = typer.Typer(help="V3.0 line-movement analyzer (Sprint 2).")
app.add_typer(movement_app, name="movement")

consensus_app = typer.Typer(help="V3.0 multi-book consensus engine (Sprint 2).")
app.add_typer(consensus_app, name="consensus")

precision_app = typer.Typer(help="V3.0 Precision Score engine (Sprint 3).")
app.add_typer(precision_app, name="precision")

clv_app = typer.Typer(help="V3.0 Closing-Line-Value tracker (Sprint 4).")
app.add_typer(clv_app, name="clv")

report_app = typer.Typer(help="V3.0 report generator (Sprint 5).")
app.add_typer(report_app, name="report")

console = Console()


@app.command()
def version() -> None:
    """Print TIGER LINE PRIME version."""
    typer.echo(f"tiger {__version__}")


@app.command()
def analyze(
    match_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    bankroll: Annotated[
        str | None,
        typer.Option("--bankroll", help="Override the bankroll in the match JSON."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output BetPlan as JSON.")] = False,
) -> None:
    """Run the decision pipeline on a single match JSON."""
    match = parse_match_file(match_file)
    if bankroll is not None:
        match = match.model_copy(update={"bankroll": Decimal(bankroll)})

    classification = classify(match)
    plan = recommend(match)

    conn = connect()
    save_match(conn, match)
    save_classification(conn, match.match_id, classification)
    save_recommendation(conn, plan, match.bankroll)

    if as_json:
        typer.echo(plan.model_dump_json(indent=2))
        return
    _render_plan(match, classification, plan)


@app.command()
def review(
    match_id: Annotated[str, typer.Argument(help="Canonical match ID.")],
    score: Annotated[str, typer.Argument(help="Actual score in 'H-A' format.")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Settle a match against its saved plan."""
    try:
        h_str, a_str = score.split("-", 1)
        home_goals = int(h_str)
        away_goals = int(a_str)
    except ValueError as e:
        raise typer.BadParameter(f"score must be 'H-A' integers; got {score!r}") from e

    conn = connect()
    match = load_match(conn, match_id)
    if match is None:
        raise typer.BadParameter(f"no saved match for {match_id!r} — run analyze first")
    plan = latest_plan(conn, match_id)
    if plan is None:
        raise typer.BadParameter(f"no saved plan for {match_id!r} — run analyze first")

    result = run_review(match, plan, home_goals, away_goals)
    save_review(conn, result)

    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    _render_review(result)


@app.command()
def backlog(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List matches with a saved plan but no review yet."""
    conn = connect()
    ids = list_backlog(conn)
    if as_json:
        typer.echo(json.dumps(ids))
        return
    if not ids:
        console.print("[dim]No matches pending review.[/dim]")
        return
    for match_id in ids:
        console.print(f"• {match_id}")


@app.command()
def stats(
    scenario: Annotated[str | None, typer.Option("--scenario")] = None,
    since: Annotated[str | None, typer.Option("--since", help="ISO date YYYY-MM-DD")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Aggregate review hit-rates by scenario."""
    conn = connect()
    summary = stats_summary(conn, scenario=scenario, since=since)
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
        return
    if not summary:
        console.print("[dim]No reviews recorded yet.[/dim]")
        return
    table = Table(title="TIGER LINE — scenario stats")
    table.add_column("Scenario")
    table.add_column("N", justify="right")
    table.add_column("Scenario %", justify="right")
    table.add_column("Corridor %", justify="right")
    table.add_column("Main win %", justify="right")
    for s, agg in summary.items():
        n = agg["n"]
        table.add_row(
            s,
            str(n),
            f"{100 * agg['scenario_correct'] / n:.0f}",
            f"{100 * agg['corridor_hit'] / n:.0f}",
            f"{100 * agg['main_win'] / n:.0f}",
        )
    console.print(table)


@snapshot_app.command("add")
def snapshot_add(
    snapshot_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate + persist one odds snapshot (SQLite + JSONL mirror)."""
    snap = parse_snapshot_file(snapshot_file)
    conn = connect()
    try:
        warnings = record_snapshot(conn, snap)
    except SnapshotRejected as e:
        console.print(f"[red]REJECTED[/red] {e}")
        raise typer.Exit(code=1) from e

    if as_json:
        typer.echo(snap.model_dump_json(indent=2))
        return
    console.print(f"[green]OK[/green] {snap.snapshot_id} → {snap.match_id}")
    for w in warnings:
        console.print(f"  [yellow]warn[/yellow] [{w.code}] {w.message}")


@snapshot_app.command("list")
def snapshot_list(
    match_id: Annotated[str, typer.Argument(help="Canonical match ID.")],
    market: Annotated[str | None, typer.Option("--market")] = None,
    bookmaker: Annotated[str | None, typer.Option("--book")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List every snapshot for a match (filter by market and/or book)."""
    conn = connect()
    rows = list_snapshots(conn, match_id, market_type=market, bookmaker=bookmaker)
    if as_json:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in rows], indent=2))
        return
    if not rows:
        console.print("[dim]No snapshots.[/dim]")
        return
    table = Table(title=f"Snapshots for {match_id}")
    table.add_column("collected_at")
    table.add_column("book")
    table.add_column("market")
    table.add_column("selection")
    table.add_column("line")
    table.add_column("price")
    for r in rows:
        table.add_row(
            r.metadata.collected_at.isoformat(),
            r.bookmaker,
            r.market_type,
            r.selection,
            "" if r.line is None else str(r.line),
            str(r.price),
        )
    console.print(table)


@snapshot_app.command("latest")
def snapshot_latest(
    match_id: Annotated[str, typer.Argument()],
    market: Annotated[str, typer.Option("--market", help="Market type, e.g. AH_full")],
    bookmaker: Annotated[str | None, typer.Option("--book")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the most recent snapshot for a (match, market) pair (and optional book)."""
    conn = connect()
    snap = latest_snapshot(conn, match_id, market_type=market, bookmaker=bookmaker)
    if snap is None:
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(snap.model_dump_json(indent=2))
        return
    console.print(
        f"{snap.metadata.collected_at.isoformat()}  "
        f"{snap.bookmaker}  {snap.market_type}  {snap.selection}  "
        f"line={snap.line if snap.line is not None else '-'}  price={snap.price}"
    )


@rules_app.command("show")
def rules_show() -> None:
    """Pretty-print the loaded YAML rule sets."""
    reset_cache()
    classes = load_classification_rules()
    corridors = load_corridor_templates()
    stakes = load_stake_bands()

    console.print("[bold]Classification rules[/bold]")
    for r in classes:
        console.print(f"  • {r.scenario:<22} → {r.reason}")
    console.print("\n[bold]Corridor templates[/bold]")
    for s, t in corridors.items():
        console.print(f"  • {s:<22} primary={t.primary} scores={t.scores}")
    console.print("\n[bold]Stake bands[/bold]")
    for level, band in stakes.items():
        console.print(
            f"  • {level:<3} min={band.min_pct} default={band.default_pct} max={band.max_pct}"
        )


@rules_app.command("validate")
def rules_validate() -> None:
    """Load + Pydantic-validate every YAML rule file; non-zero exit on failure."""
    reset_cache()
    try:
        load_classification_rules()
        load_corridor_templates()
        load_stake_bands()
    except Exception as e:
        console.print(f"[red]Rule validation failed:[/red] {e}")
        raise typer.Exit(code=1) from e
    console.print("[green]Rules OK.[/green]")


# ──────────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────────


_LEVEL_STYLE = {
    "A+": "bold green",
    "A": "green",
    "B": "yellow",
    "C": "dim",
    "D": "red",
}


def _render_plan(match, classification, plan) -> None:
    header = Table.grid(padding=(0, 2))
    header.add_row(
        f"[bold]{match.home} vs {match.away}[/bold]",
        f"[dim]{match.match_id}[/dim]",
    )
    header.add_row(
        f"Scenario: [bold]{classification.scenario}[/bold]",
        f"Confidence: {classification.confidence}",
    )
    console.print(header)

    if plan.main_bet is None:
        console.print("[red]SKIP[/red] — no main bet.")
    else:
        leg = plan.main_bet
        style = _LEVEL_STYLE.get(leg.level, "white")
        console.print(
            f"[{style}]Main: {leg.selection} ({leg.level}) "
            f"@ {leg.stake_pct} = {leg.stake_amount}[/{style}]"
        )

    if plan.secondary:
        console.print("[bold]Secondary[/bold]")
        for leg in plan.secondary:
            console.print(
                f"  • {leg.selection} ({leg.level}) "
                f"@ {leg.stake_pct} = {leg.stake_amount}"
            )
    if plan.correct_score:
        console.print("[bold]Correct-score sprinkles[/bold]")
        for leg in plan.correct_score:
            console.print(f"  • {leg.selection} @ {leg.stake_amount}")
    if plan.avoid:
        console.print("[bold red]Avoid[/bold red]")
        for note in plan.avoid:
            console.print(f"  • {note}")
    if plan.reserve_live:
        console.print("[bold]Reserve for live[/bold]")
        for note in plan.reserve_live:
            console.print(f"  • {note}")


def _render_review(r) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_row("[bold]Review[/bold]", f"[dim]{r.match_id}[/dim]")
    grid.add_row(f"Actual: {r.actual_score[0]}-{r.actual_score[1]}", f"Score: {r.score}/100")
    grid.add_row(
        f"Scenario correct: {r.scenario_correct}", f"Corridor hit: {r.corridor_hit}"
    )
    grid.add_row(f"Main: {r.main_result}", "")
    console.print(grid)
    if r.suggestions:
        console.print("[bold]Suggestions[/bold]")
        for s in r.suggestions:
            console.print(f"  • {s}")


# ──────────────────────────────────────────────────────────────────────────
# V3.0 — Movement
# ──────────────────────────────────────────────────────────────────────────


@movement_app.command("analyze")
def movement_analyze(
    match_id: Annotated[str, typer.Argument()],
    market: Annotated[str, typer.Option("--market", help="Market type, e.g. AH_full")],
    bookmaker: Annotated[str | None, typer.Option("--book")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compute line movement for a (match, market) from saved snapshots."""
    conn = connect()
    snaps = list_snapshots(conn, match_id, market_type=market, bookmaker=bookmaker)
    if len(snaps) < 2:
        console.print(f"[red]Need at least 2 snapshots, found {len(snaps)}.[/red]")
        raise typer.Exit(code=1)
    report = analyze_movement(snaps)
    if report is None:
        console.print("[red]Movement analysis returned None unexpectedly.[/red]")
        raise typer.Exit(code=1)
    if as_json:
        from dataclasses import asdict
        payload = {k: (str(v) if hasattr(v, "isoformat") or v.__class__.__name__ == "Decimal" else v)
                   for k, v in asdict(report).items()}
        typer.echo(json.dumps(payload, default=str, indent=2))
        return
    console.print(
        f"[bold]{match_id}[/bold] {market}"
        + (f" @ {bookmaker}" if bookmaker else " (all books merged)")
    )
    console.print(f"Line: {report.opening_line} → {report.current_line} (Δ {report.line_delta})")
    console.print(f"Price: {report.opening_price} → {report.current_price} (Δ {report.price_delta})")
    console.print(f"Direction: [bold]{report.direction}[/bold]  Velocity: {report.velocity}")
    if report.crosses_key_handicaps:
        keys = ", ".join(str(k) for k in report.crosses_key_handicaps)
        console.print(f"Crossed key handicaps: [yellow]{keys}[/yellow]")


# ──────────────────────────────────────────────────────────────────────────
# V3.0 — Consensus
# ──────────────────────────────────────────────────────────────────────────


@consensus_app.command("show")
def consensus_show(
    match_id: Annotated[str, typer.Argument()],
    market: Annotated[str, typer.Option("--market", help="Market type, e.g. AH_full")],
    user_book: Annotated[str | None, typer.Option("--user-book")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compute multi-book consensus from the latest snapshot per book."""
    conn = connect()
    all_snaps = list_snapshots(conn, match_id, market_type=market)
    if not all_snaps:
        console.print("[red]No snapshots for that (match, market).[/red]")
        raise typer.Exit(code=1)
    # One latest snapshot per bookmaker.
    by_book: dict[str, object] = {}
    for s in all_snaps:
        by_book[s.bookmaker] = s
    latest_per_book = list(by_book.values())  # type: ignore[assignment]
    report = compute_consensus(latest_per_book, user_bookmaker=user_book)  # type: ignore[arg-type]
    if report is None:
        console.print("[red]Consensus engine returned None.[/red]")
        raise typer.Exit(code=1)
    if as_json:
        from dataclasses import asdict
        typer.echo(json.dumps(asdict(report), default=str, indent=2))
        return
    console.print(f"[bold]{match_id}[/bold] {market} — {report.n_books} books")
    console.print(f"Consensus line: [bold]{report.consensus_line}[/bold]  Agreement: {report.agreement}")
    if user_book:
        console.print(
            f"User book ({user_book}) line: {report.user_platform_line} "
            f"→ [bold]{report.user_platform_edge}[/bold] (margin {report.edge_magnitude})"
        )
    if report.outlier_books:
        console.print(f"Outliers: [yellow]{', '.join(report.outlier_books)}[/yellow]")


# ──────────────────────────────────────────────────────────────────────────
# V3.0 — Precision Score
# ──────────────────────────────────────────────────────────────────────────


@precision_app.command("score")
def precision_score(
    match_id: Annotated[str, typer.Argument()],
    market: Annotated[str, typer.Option("--market")] = "AH_full",
    user_book: Annotated[str | None, typer.Option("--user-book")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Full pipeline → Precision Score (0..100) + level."""
    conn = connect()
    match = load_match(conn, match_id)
    if match is None:
        console.print(f"[red]No saved match for {match_id!r} — run analyze first.[/red]")
        raise typer.Exit(code=1)
    classification = classify(match)
    plan = recommend(match)
    harness_verdict = evaluate_harness(match, classification)
    corridor = plan.correct_score  # use plan's corridor sprinkles as proxy

    snaps = list_snapshots(conn, match_id, market_type=market)
    movement_report = None
    if len(snaps) >= 2:
        movement_report = analyze_movement(snaps)
    consensus_report = None
    if snaps:
        by_book: dict[str, object] = {}
        for s in snaps:
            by_book[s.bookmaker] = s
        consensus_report = compute_consensus(list(by_book.values()), user_bookmaker=user_book)  # type: ignore[arg-type]

    from tigerline.models import ScoreCorridor
    # The precision engine wants a ScoreCorridor — reconstruct from plan's CS legs.
    scores = [(int(leg.selection.split("-")[0]), int(leg.selection.split("-")[1]))
              for leg in corridor]
    if not scores:
        scores = [(1, 1)]
    corridor_obj = ScoreCorridor(scores=scores, primary=scores[0])

    score = score_precision(
        classification=classification,
        corridor=corridor_obj,
        harness=harness_verdict,
        movement=movement_report,
        consensus=consensus_report,
        snapshots=snaps,
        risk_flags=match.risk_flags,
    )
    if as_json:
        from dataclasses import asdict
        typer.echo(json.dumps(asdict(score), default=str, indent=2))
        return
    style = _LEVEL_STYLE.get(score.level, "white")
    console.print(f"[bold]{match.home} vs {match.away}[/bold]  ({classification.scenario})")
    console.print(f"Precision: [{style}]{score.score}/100 → {score.level}[/{style}]")
    for ch, val in score.channel_scores.items():
        console.print(f"  • {ch:<22} {val}")
    if score.penalties_applied:
        console.print("[yellow]Penalties:[/yellow]")
        for code, val in score.penalties_applied.items():
            console.print(f"  - {code:<30} -{val}")
    for n in score.notes:
        console.print(f"  · {n}")


# ──────────────────────────────────────────────────────────────────────────
# V3.0 — CLV
# ──────────────────────────────────────────────────────────────────────────


@clv_app.command("summary")
def clv_show(
    since: Annotated[str | None, typer.Option("--since", help="ISO date YYYY-MM-DD")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Aggregate beat / worse / flat percentages across all CLV records."""
    conn = connect()
    summary = clv_summary(conn, since=since)
    if as_json:
        typer.echo(json.dumps(summary, default=str, indent=2))
        return
    n = summary["total"]
    if n == 0:
        console.print("[dim]No CLV records yet.[/dim]")
        return
    console.print(f"Total CLV records: {n}")
    console.print(f"  Beat:  {summary['beat']}  ({summary['beat_rate'] * 100:.1f}%)")
    console.print(f"  Worse: {summary['worse']}")
    console.print(f"  Flat:  {summary['flat']}")


# ──────────────────────────────────────────────────────────────────────────
# V3.0 — Reports
# ──────────────────────────────────────────────────────────────────────────


@report_app.command("daily")
def report_daily(
    match_id: Annotated[str, typer.Argument()],
    tier: Annotated[str, typer.Option("--tier")] = "pro",
) -> None:
    """Daily pre-match brief (uses last saved analyze + snapshots)."""
    conn = connect()
    match = load_match(conn, match_id)
    if match is None:
        raise typer.BadParameter(f"no saved match for {match_id!r}")
    plan = latest_plan(conn, match_id)
    if plan is None:
        raise typer.BadParameter(f"no saved plan for {match_id!r}")
    classification = classify(match)
    ctx = ReportContext(match=match, classification=classification, plan=plan)
    typer.echo(daily_pre_match_report(ctx, tier=tier))  # type: ignore[arg-type]


@report_app.command("review")
def report_review(
    match_id: Annotated[str, typer.Argument()],
    score: Annotated[str, typer.Argument(help="Score in 'H-A' format")],
    tier: Annotated[str, typer.Option("--tier")] = "pro",
) -> None:
    """Post-match review report."""
    try:
        h, a = score.split("-", 1)
        hg, ag = int(h), int(a)
    except ValueError as e:
        raise typer.BadParameter(f"score must be 'H-A' integers; got {score!r}") from e
    conn = connect()
    match = load_match(conn, match_id)
    plan = latest_plan(conn, match_id)
    if match is None or plan is None:
        raise typer.BadParameter(f"no saved match/plan for {match_id!r}")
    result = run_review(match, plan, hg, ag)
    typer.echo(post_match_review_report(match, plan, result, tier=tier))  # type: ignore[arg-type]


@report_app.command("calibration")
def report_calibration(
    since: Annotated[str | None, typer.Option("--since")] = None,
    tier: Annotated[str, typer.Option("--tier")] = "vip",
) -> None:
    """Weekly calibration report (Brier / log-loss / per-bucket hit rate)."""
    conn = connect()
    cal = calibrate(conn, since=since)
    typer.echo(weekly_calibration_report(cal, tier=tier))  # type: ignore[arg-type]


@report_app.command("alert")
def report_alert(
    match_id: Annotated[str, typer.Argument()],
    market: Annotated[str, typer.Option("--market")],
    tier: Annotated[str, typer.Option("--tier")] = "pro",
) -> None:
    """Market-movement alert for one (match, market)."""
    conn = connect()
    match = load_match(conn, match_id)
    if match is None:
        raise typer.BadParameter(f"no saved match for {match_id!r}")
    snaps = list_snapshots(conn, match_id, market_type=market)
    if len(snaps) < 2:
        raise typer.BadParameter(f"need ≥2 snapshots for {match_id}/{market}; got {len(snaps)}")
    mv = analyze_movement(snaps)
    if mv is None:
        raise typer.BadParameter("movement engine returned None")
    typer.echo(market_alert_report(match, mv, tier=tier))  # type: ignore[arg-type]


@report_app.command("avoid")
def report_avoid(
    match_ids: Annotated[list[str], typer.Argument(help="Match IDs to compile avoid list for")],
    tier: Annotated[str, typer.Option("--tier")] = "basic",
) -> None:
    """Avoid list across one or more matches (reads latest plan per match)."""
    conn = connect()
    plans = []
    for mid in match_ids:
        p = latest_plan(conn, mid)
        if p is not None:
            plans.append(p)
    typer.echo(avoid_list_report(plans, tier=tier))  # type: ignore[arg-type]
