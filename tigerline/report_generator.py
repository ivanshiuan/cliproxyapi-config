"""V3.0 Sprint 5 — Report Generator.

Produces five report types as plain-text Markdown that can be copy-pasted into
Slack / Notion / email:

- daily_pre_match  — what to bet (or skip) for one match
- market_alert      — line moved → reassess
- post_match_review — score + suggestions after full-time
- weekly_calibration — Brier / log-loss / per-bucket hit rate
- avoid_list        — what NOT to touch for the day

Every report goes through ``compliance_guard.enforce`` before return so the
disclaimer is always appended and forbidden phrases always blocked.

Reports also have **tiers**: ``free`` / ``basic`` / ``pro`` / ``vip`` /
``enterprise``. Tier filtering happens at the renderer — pro+ sees stake $$,
basic sees scenario only, free sees match list only. The tier is passed in;
the generator never decides on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from tigerline.calibration_engine import CalibrationReport
from tigerline.compliance_guard import enforce
from tigerline.consensus_engine import ConsensusReport
from tigerline.line_movement_analyzer import MovementReport
from tigerline.models import BetPlan, MatchClassification, MatchInput, ReviewResult
from tigerline.precision_score_engine import PrecisionScore

Tier = Literal["free", "basic", "pro", "vip", "enterprise"]


@dataclass(frozen=True)
class ReportContext:
    """Bundle of artefacts the generator may reference."""

    match: MatchInput
    classification: MatchClassification
    plan: BetPlan
    precision: PrecisionScore | None = None
    movement: MovementReport | None = None
    consensus: ConsensusReport | None = None


def daily_pre_match_report(
    ctx: ReportContext,
    *,
    tier: Tier = "pro",
    now: datetime | None = None,
) -> str:
    """Pre-match brief for one match."""
    stamp = (now or _now_utc()).isoformat(timespec="minutes")
    lines: list[str] = [
        f"# Pre-Match Brief — {ctx.match.home} vs {ctx.match.away}",
        f"_match_id: `{ctx.match.match_id}` · generated {stamp}_",
        "",
    ]

    if tier in ("free",):
        lines.append(f"Scenario: **{ctx.classification.scenario}**")
        return enforce("\n".join(lines))

    lines += [
        f"Scenario: **{ctx.classification.scenario}** (confidence {ctx.classification.confidence})",
        f"Score corridor: {_corridor_str(ctx.plan)}",
    ]

    if ctx.precision and tier in ("pro", "vip", "enterprise"):
        lines += [
            f"Precision score: **{ctx.precision.score}/100** → level **{ctx.precision.level}**",
        ]
        if ctx.precision.notes:
            lines.append("Signals:")
            lines += [f"- {n}" for n in ctx.precision.notes]

    if ctx.plan.main_bet and tier in ("pro", "vip", "enterprise"):
        leg = ctx.plan.main_bet
        lines += [
            "",
            f"**Main**: {leg.selection} ({leg.level}) — stake {leg.stake_amount} ({leg.stake_pct} of bankroll)",
        ]

    if ctx.plan.secondary and tier in ("vip", "enterprise"):
        lines.append("Secondary:")
        lines += [f"- {leg.selection} ({leg.level}) — {leg.stake_amount}" for leg in ctx.plan.secondary]

    if ctx.plan.avoid:
        lines += ["", "Avoid:"]
        lines += [f"- {a}" for a in ctx.plan.avoid]

    if ctx.movement and tier in ("vip", "enterprise"):
        lines += [
            "",
            f"Market movement: {ctx.movement.direction} "
            f"({ctx.movement.opening_line} → {ctx.movement.current_line}, velocity {ctx.movement.velocity})",
        ]

    if ctx.consensus and tier in ("vip", "enterprise"):
        lines.append(
            f"Consensus: {ctx.consensus.consensus_line} across {ctx.consensus.n_books} books "
            f"({ctx.consensus.agreement} agreement) — your book: {ctx.consensus.user_platform_edge}"
        )

    return enforce("\n".join(lines))


def market_alert_report(
    match: MatchInput,
    movement: MovementReport,
    *,
    tier: Tier = "pro",
    now: datetime | None = None,
) -> str:
    stamp = (now or _now_utc()).isoformat(timespec="minutes")
    lines = [
        f"# Market Alert — {match.home} vs {match.away}",
        f"_match_id: `{match.match_id}` · generated {stamp}_",
        "",
        f"Movement: **{movement.direction}** "
        f"({movement.opening_line} → {movement.current_line}, velocity {movement.velocity})",
        f"Line delta: {movement.line_delta} · price delta: {movement.price_delta}",
    ]
    if movement.crosses_key_handicaps:
        keys = ", ".join(str(k) for k in movement.crosses_key_handicaps)
        lines.append(f"Crossed key handicaps: {keys}")
    if tier in ("vip", "enterprise"):
        lines.append("Reassess your stake before placing — line moved past a sharp threshold.")
    return enforce("\n".join(lines))


def post_match_review_report(
    match: MatchInput,
    plan: BetPlan,
    result: ReviewResult,
    *,
    tier: Tier = "pro",
    now: datetime | None = None,
) -> str:
    stamp = (now or _now_utc()).isoformat(timespec="minutes")
    lines = [
        f"# Post-Match Review — {match.home} vs {match.away}",
        f"_match_id: `{match.match_id}` · generated {stamp}_",
        "",
        f"Actual score: **{result.actual_score[0]}-{result.actual_score[1]}**",
        f"Scenario correct: **{result.scenario_correct}**",
        f"Corridor hit: **{result.corridor_hit}**",
        f"Main result: **{result.main_result}** · scorecard: **{result.score}/100**",
    ]
    if plan.main_bet:
        lines.append(f"Main bet was: {plan.main_bet.selection} @ {plan.main_bet.stake_amount}")
    if result.suggestions:
        lines += ["", "Suggestions:"]
        lines += [f"- {s}" for s in result.suggestions]
    return enforce("\n".join(lines))


def weekly_calibration_report(
    calibration: CalibrationReport,
    *,
    tier: Tier = "vip",
    now: datetime | None = None,
) -> str:
    stamp = (now or _now_utc()).isoformat(timespec="minutes")
    lines = [
        "# Weekly Calibration",
        f"_generated {stamp}_",
        "",
        f"Reviewed matches: **{calibration.n_reviewed}**",
        f"Brier score: **{calibration.brier:.3f}** (lower = better)",
        f"Log loss: **{calibration.log_loss:.3f}**",
        f"Expected Calibration Error: **{calibration.expected_calibration_error:.3f}**",
        "",
        "## Per-confidence-bucket performance",
    ]
    if not calibration.buckets:
        lines.append("_no buckets — insufficient data_")
    else:
        lines.append("| Bucket | n | avg predicted | corridor hit % | main win % |")
        lines.append("|---|---|---|---|---|")
        for b in calibration.buckets:
            lines.append(
                f"| {b.bucket_lo}-{b.bucket_hi - 1} | {b.n} | "
                f"{b.avg_predicted:.2f} | {b.observed_hit_rate * 100:.0f}% | "
                f"{b.main_win_rate * 100:.0f}% |"
            )
    return enforce("\n".join(lines))


def avoid_list_report(
    plans: list[BetPlan],
    *,
    tier: Tier = "basic",
    now: datetime | None = None,
) -> str:
    stamp = (now or _now_utc()).isoformat(timespec="minutes")
    lines = [
        "# Avoid List",
        f"_generated {stamp}_",
        "",
    ]
    if not plans:
        lines.append("_no avoid items_")
    else:
        for p in plans:
            if not p.avoid:
                continue
            lines.append(f"## {p.match_id} ({p.scenario})")
            lines += [f"- {a}" for a in p.avoid]
    return enforce("\n".join(lines))


def _corridor_str(plan: BetPlan) -> str:
    if not plan.correct_score:
        return "_(no corridor sprinkles)_"
    return ", ".join(leg.selection for leg in plan.correct_score)


def _now_utc() -> datetime:
    """Indirection so tests can monkeypatch."""
    # We don't import datetime.now() at top to make patching easy.
    from datetime import datetime as _dt

    return _dt.now(UTC)


__all__ = [
    "ReportContext",
    "Tier",
    "avoid_list_report",
    "daily_pre_match_report",
    "market_alert_report",
    "post_match_review_report",
    "weekly_calibration_report",
]
