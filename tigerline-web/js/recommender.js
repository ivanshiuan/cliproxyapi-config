/* =====================================================================
 * recommender.js — composes classifier + corridor + harness + stake into
 * a BetPlan. Ports tigerline/recommender.py.
 * ===================================================================== */
"use strict";

import { classify } from "./classifier.js";
import { buildCorridor } from "./corridor.js";
import { evaluateHarness } from "./harness.js";
import { allocate, pickLevel } from "./stake.js";
import { fmtDec, roundHalfUp } from "./decimal.js";

const CORRECT_SCORE_LEVEL = "C";
const CORRECT_SCORE_PCT_BUDGET = 0.04;

/** @param {import('./types.js').MatchInput} match */
export function recommend(match) {
  const classification = classify(match);
  const corridor = buildCorridor(classification.scenario, match);
  const verdict = evaluateHarness(match, classification);

  const mainBet = buildMain(match, classification, verdict);
  const secondary = buildSecondary(match, classification, verdict);
  const correctScore = buildCorrectScore(match, corridor, verdict);
  const avoid = buildAvoid(match, classification);
  const reserveLive = buildReserveLive(classification);

  return {
    match_id: match.match_id,
    scenario: classification.scenario,
    classification,
    corridor,
    harness: verdict,
    main_bet: mainBet,
    secondary,
    correct_score: correctScore,
    avoid,
    reserve_live: reserveLive,
  };
}

function favTeam(match) {
  return match.market.favorite === "home" ? match.home : match.away;
}

function mainSelection(match, scenario) {
  const fav = favTeam(match);
  switch (scenario) {
    case "two_goal_landing":  return `${fav} -1.5`;
    case "must_win_pressure": return `${fav} -1`;
    case "narrow_win":        return `${fav} -0.5`;
    case "goal_market":       return `Over ${fmtDec(match.totals.line, 2)}`;
    case "pressure_under":    return `Under ${fmtDec(match.totals.line, 2)}`;
    default: return null;
  }
}

function marketKind(scenario) {
  return scenario === "goal_market" || scenario === "pressure_under" ? "totals" : "AH";
}

function buildMain(match, classification, verdict) {
  if (verdict.adjustment === "skip") return null;
  const selection = mainSelection(match, classification.scenario);
  if (!selection) return null;
  const level = pickLevel(classification.confidence, verdict.adjustment);
  const { pct, amount } = allocate(level, match.bankroll, verdict.adjustment);
  if (amount === 0) return null;
  return {
    market: marketKind(classification.scenario),
    selection,
    level,
    stake_pct: pct,
    stake_amount: amount,
  };
}

function buildSecondary(match, classification, verdict) {
  if (verdict.adjustment === "skip") return [];
  const legs = [];
  const { pct, amount } = allocate("C", match.bankroll, verdict.adjustment);
  if (amount === 0) return legs;
  if (classification.scenario === "two_goal_landing") {
    legs.push({
      market: "totals",
      selection: `Over ${fmtDec(match.totals.line, 2)}`,
      level: "C",
      stake_pct: pct,
      stake_amount: amount,
    });
  }
  if (classification.scenario === "pressure_under") {
    legs.push({ market: "BTTS", selection: "No", level: "C", stake_pct: pct, stake_amount: amount });
  }
  return legs;
}

function buildCorrectScore(match, corridor, verdict) {
  if (verdict.adjustment === "skip") return [];
  const candidates = corridor.scores.slice(0, 3);
  if (!candidates.length) return [];
  const perLegPct = Math.round((CORRECT_SCORE_PCT_BUDGET / candidates.length) * 1000) / 1000;
  const legs = [];
  for (const [h, a] of candidates) {
    const amount = roundHalfUp(match.bankroll * perLegPct);
    if (amount <= 0) continue;
    legs.push({
      market: "correct_score",
      selection: `${h}-${a}`,
      level: CORRECT_SCORE_LEVEL,
      stake_pct: perLegPct,
      stake_amount: amount,
    });
  }
  return legs;
}

function buildAvoid(match, classification) {
  const fav = favTeam(match);
  const out = [];
  switch (classification.scenario) {
    case "two_goal_landing":  out.push(`${fav} -2.5 (no protection if 2-0 / 2-1)`); break;
    case "pressure_under":    out.push(`Over ${fmtDec(match.totals.line, 2)} (both teams need a tie tactically)`); break;
    case "rotation_trap":     out.push(`${fav} -1 or deeper`); break;
    case "goal_market":       out.push(`${fav} -1.5 (line is shallow for a reason)`); break;
  }
  return out;
}

function buildReserveLive(classification) {
  switch (classification.scenario) {
    case "rotation_trap":    return ["Hold full powder; revisit at HT if underdog leads."];
    case "pressure_under":   return ["Reserve for live Under if 0-0 at HT — line will drift."];
    case "two_goal_landing": return ["Reserve 1 unit for live Over if 1-0 by 35'."];
    default: return [];
  }
}
