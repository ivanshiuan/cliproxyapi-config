"""HARNESS-GATE — risk control. Only ever upgrade / downgrade / skip / pass-through.

Never picks the bet. Reads scenario + classifier confidence + team_need + risk
flags and returns a verdict the recommender uses to nudge stake levels.
"""

from __future__ import annotations

from decimal import Decimal

from tigerline.models import (
    HarnessVerdict,
    MatchClassification,
    MatchInput,
)


def evaluate(
    match: MatchInput,
    classification: MatchClassification,
) -> HarnessVerdict:
    """Decide whether to upgrade, normalize, downgrade, or skip the main bet."""
    notes: list[str] = []
    scenario = classification.scenario

    # Hard skip rules — these short-circuit before any upgrade considerations.
    if scenario in ("rotation_trap", "unclear_skip"):
        notes.append(f"scenario={scenario} forces skip")
        return HarnessVerdict(adjustment="skip", notes=notes)

    risk_flag_count = len(match.risk_flags)
    fav_need = match.team_need.home if match.market.favorite == "home" else match.team_need.away
    und_need = match.team_need.away if match.market.favorite == "home" else match.team_need.home

    # Skip when the favorite is already qualified / rotating despite the rule
    # not catching it (e.g. the trap rule's handicap_abs_gte threshold).
    if "favorite_already_qualified" in match.risk_flags:
        notes.append("risk flag: favorite already qualified")
        return HarnessVerdict(adjustment="skip", notes=notes)

    # Downgrade triggers — accumulate, do not short-circuit.
    downgrade_reasons: list[str] = []
    if classification.confidence < Decimal("0.5"):
        downgrade_reasons.append(f"low classifier confidence ({classification.confidence})")
    if und_need == "must_win" and scenario == "two_goal_landing":
        downgrade_reasons.append("underdog must_win against two-goal handicap")
    if match.group_context.same_time_kickoff and scenario != "pressure_under":
        downgrade_reasons.append("parallel kickoff in final round")
    if risk_flag_count >= 2:
        downgrade_reasons.append(f"{risk_flag_count} risk flags raised")

    # Upgrade triggers — only fire when downgrade list is empty.
    upgrade_reasons: list[str] = []
    if not downgrade_reasons:
        if classification.confidence >= Decimal("0.85") and fav_need in ("must_win", "neutral"):
            upgrade_reasons.append("high confidence + favorite has motive")
        if (
            scenario == "two_goal_landing"
            and classification.confidence >= Decimal("0.85")
            and und_need in ("rotation", "draw_ok")
        ):
            upgrade_reasons.append("clean two-goal landing with passive underdog")

    if upgrade_reasons:
        notes.extend(upgrade_reasons)
        return HarnessVerdict(adjustment="upgrade", notes=notes)
    if downgrade_reasons:
        notes.extend(downgrade_reasons)
        return HarnessVerdict(adjustment="downgrade", notes=notes)

    notes.append("no risk signals; proceed at default size")
    return HarnessVerdict(adjustment="normal", notes=notes)


__all__ = ["evaluate"]
