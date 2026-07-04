/* =====================================================================
 * harness.js — risk gate. Never picks bets. Ports tigerline/harness.py.
 * ===================================================================== */
"use strict";

import { dec } from "./decimal.js";

const SKIP_SCENARIOS = new Set(["rotation_trap", "unclear_skip"]);

/**
 * @param {import('./types.js').MatchInput} match
 * @param {{scenario:string, confidence:number, reasons:string[]}} classification
 */
export function evaluateHarness(match, classification) {
  const notes = [];
  const { scenario, confidence } = classification;

  if (SKIP_SCENARIOS.has(scenario)) {
    notes.push(`scenario=${scenario} forces skip`);
    return { adjustment: "skip", notes };
  }

  if (match.risk_flags.includes("favorite_already_qualified")) {
    notes.push("risk flag: favorite already qualified");
    return { adjustment: "skip", notes };
  }

  const favNeed = match.market.favorite === "home" ? match.team_need.home : match.team_need.away;
  const undNeed = match.market.favorite === "home" ? match.team_need.away : match.team_need.home;
  const riskFlagCount = match.risk_flags.length;

  const downgrade = [];
  if (dec.lt(confidence, 0.5)) downgrade.push(`low classifier confidence (${confidence})`);
  if (undNeed === "must_win" && scenario === "two_goal_landing") {
    downgrade.push("underdog must_win against two-goal handicap");
  }
  if (match.group_context.same_time_kickoff && scenario !== "pressure_under") {
    downgrade.push("parallel kickoff in final round");
  }
  if (riskFlagCount >= 2) downgrade.push(`${riskFlagCount} risk flags raised`);

  const upgrade = [];
  if (!downgrade.length) {
    if (dec.gte(confidence, 0.85) && (favNeed === "must_win" || favNeed === "neutral")) {
      upgrade.push("high confidence + favorite has motive");
    }
    if (
      scenario === "two_goal_landing" &&
      dec.gte(confidence, 0.85) &&
      (undNeed === "rotation" || undNeed === "draw_ok")
    ) {
      upgrade.push("clean two-goal landing with passive underdog");
    }
  }

  if (upgrade.length) return { adjustment: "upgrade", notes: [...notes, ...upgrade] };
  if (downgrade.length) return { adjustment: "downgrade", notes: [...notes, ...downgrade] };
  notes.push("no risk signals; proceed at default size");
  return { adjustment: "normal", notes };
}
