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
from tigerline.classifier import classify
from tigerline.parser import load_match as parse_match_file
from tigerline.recommender import recommend
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
    list_backlog,
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
