/* =====================================================================
 * review.js — post-match settlement + 0-100 scorecard.
 * Ports tigerline/review.py.
 * ===================================================================== */
"use strict";

import { classify } from "./classifier.js";
import { buildCorridor } from "./corridor.js";
import { dec } from "./decimal.js";

const SCENARIO_WEIGHT = 40;
const CORRIDOR_WEIGHT = 30;
const MAIN_WEIGHT = 30;

const MAIN_POINTS = {
  win: MAIN_WEIGHT,
  half_win: Math.floor((MAIN_WEIGHT * 2) / 3),
  push: Math.floor(MAIN_WEIGHT / 2),
  half_lose: Math.floor(MAIN_WEIGHT / 3),
  lose: 0,
  void: Math.floor(MAIN_WEIGHT / 2),
  skipped: Math.floor(MAIN_WEIGHT / 2),
};

/**
 * @param {import('./types.js').MatchInput} match
 * @param {ReturnType<import('./recommender.js').recommend>} plan
 * @param {number} homeGoals
 * @param {number} awayGoals
 */
export function review(match, plan, homeGoals, awayGoals) {
  const actual = [homeGoals, awayGoals];
  const fresh = classify(match);
  const scenarioCorrect = fresh.scenario === plan.scenario;

  const corridor = buildCorridor(plan.scenario, match);
  const corridorHit = corridor.scores.some(([h, a]) => h === homeGoals && a === awayGoals);

  const mainResult = settleMain(match, plan.main_bet, homeGoals, awayGoals);
  const score = scorecard(scenarioCorrect, corridorHit, mainResult);
  const suggestions = suggest(plan.scenario, scenarioCorrect, corridorHit, mainResult, actual);

  return {
    match_id: match.match_id,
    actual_score: actual,
    scenario_correct: scenarioCorrect,
    corridor_hit: corridorHit,
    main_result: mainResult,
    score,
    suggestions,
  };
}

function settleMain(match, leg, hg, ag) {
  if (!leg) return "skipped";
  if (leg.market === "AH") return settleAH(match, leg, hg, ag);
  if (leg.market === "totals") return settleTotals(match, leg, hg + ag);
  if (leg.market === "correct_score") return settleCorrectScore(leg, hg, ag);
  if (leg.market === "BTTS") return settleBtts(leg, hg, ag);
  return "void";
}

function parseAhSelection(selection, match) {
  const idx = selection.lastIndexOf(" ");
  if (idx < 0) return { line: null, side: "home" };
  const team = selection.slice(0, idx);
  const lineStr = selection.slice(idx + 1);
  const line = Number(lineStr);
  if (!Number.isFinite(line)) return { line: null, side: "home" };
  return { line, side: team === match.home ? "home" : "away" };
}

function classifyAhOutcome(settled) {
  if (dec.gt(settled, 0.25)) return "win";
  if (dec.eq(settled, 0.25)) return "half_win";
  if (dec.eq(settled, 0)) return "push";
  if (dec.eq(settled, -0.25)) return "half_lose";
  return "lose";
}

function settleAH(match, leg, hg, ag) {
  const { line, side } = parseAhSelection(leg.selection, match);
  if (line == null) return "void";
  const margin = side === "home" ? hg - ag : ag - hg;
  return classifyAhOutcome(margin + line);
}

function settleTotals(match, leg, total) {
  const line = match.totals.line;
  const lower = leg.selection.toLowerCase();
  let diff;
  if (lower.startsWith("over")) diff = total - line;
  else if (lower.startsWith("under")) diff = line - total;
  else return "void";
  return classifyAhOutcome(diff);
}

function settleCorrectScore(leg, hg, ag) {
  const [hStr, aStr] = leg.selection.split("-");
  const h = parseInt(hStr, 10);
  const a = parseInt(aStr, 10);
  if (Number.isNaN(h) || Number.isNaN(a)) return "void";
  return h === hg && a === ag ? "win" : "lose";
}

function settleBtts(leg, hg, ag) {
  const both = hg > 0 && ag > 0;
  const sel = leg.selection.toLowerCase();
  if (sel === "yes") return both ? "win" : "lose";
  if (sel === "no") return both ? "lose" : "win";
  return "void";
}

function scorecard(scenarioOk, corridorOk, main) {
  let s = 0;
  if (scenarioOk) s += SCENARIO_WEIGHT;
  if (corridorOk) s += CORRIDOR_WEIGHT;
  s += MAIN_POINTS[main] ?? 0;
  return Math.min(100, Math.max(0, s));
}

function suggest(scenario, scenarioOk, corridorOk, main, actual) {
  const out = [];
  if (!scenarioOk) {
    out.push(
      `Scenario mismatch — re-classified differently on review. Inspect classification_rules.yaml ordering for ${scenario}.`,
    );
  }
  if (scenarioOk && !corridorOk) {
    out.push(
      `Scenario ${scenario} held but corridor missed ${actual[0]}-${actual[1]}. Consider widening corridor_templates.yaml for blowouts / edge scores.`,
    );
  }
  if (main === "lose" && scenarioOk && corridorOk) {
    out.push(
      "Engine read the match correctly but main bet lost — review main-bet line choice (too aggressive vs. corridor signal?).",
    );
  }
  if (main === "skipped" && corridorOk) {
    out.push("Skip stood — corridor caught the score; harness held the line.");
  }
  return out;
}
