/* =====================================================================
 * classifier.js — 7-scenario decision tree. Faithful port of
 * tigerline/classifier.py + config/classification_rules.yaml.
 *
 * Rules are embedded as data (not YAML at runtime) so this bundle stays
 * a single static asset with zero fetch.
 * ===================================================================== */
"use strict";

import { dec } from "./decimal.js";

/** In-order rule list. First full match wins. `reason` mirrors the YAML entry. */
export const CLASSIFICATION_RULES = [
  {
    scenario: "rotation_trap",
    when: { favorite_need: "rotation", handicap_abs_gte: 1.0 },
    reason: "Market priced favorite as if at full strength but favorite is rotating.",
  },
  {
    scenario: "pressure_under",
    when: {
      handicap_abs_lte: 0.5,
      totals_lte: 2.25,
      either_need_in: ["must_win"],
      final_round: true,
    },
    reason: "Shallow handicap + low totals + must-win pressure in final round.",
  },
  {
    scenario: "must_win_pressure",
    when: {
      handicap_abs_gte: 0.75,
      handicap_abs_lte: 1.5,
      totals_gte: 2.5,
      favorite_need: "must_win",
    },
    reason: "Favorite must win, market gives them headroom but not a blowout line.",
  },
  {
    scenario: "two_goal_landing",
    when: {
      handicap_abs_gte: 1.5,
      totals_gte: 2.75,
      favorite_need_in: ["must_win", "neutral"],
      underdog_need_in: ["rotation", "draw_ok", "neutral", "unknown"],
    },
    reason: "Two-goal handicap + open totals + favorite not rotating.",
  },
  {
    scenario: "goal_market",
    when: { handicap_abs_lte: 1.0, totals_gte: 2.75 },
    reason: "Shallow handicap but open totals — bet the goal market, not the side.",
  },
  {
    scenario: "narrow_win",
    when: { handicap_abs_gte: 0.25, handicap_abs_lte: 0.75, totals_lte: 2.5 },
    reason: "Shallow handicap + medium totals — favorite edges by one.",
  },
];

const HIGH_CONF = 0.9;
const LOW_CONF = 0.4;

function favUnd(match) {
  return match.market.favorite === "home"
    ? [match.team_need.home, match.team_need.away]
    : [match.team_need.away, match.team_need.home];
}

function ruleMatches(w, hAbs, tLine, favNeed, undNeed, match) {
  if (w.handicap_abs_gte != null && !dec.gte(hAbs, w.handicap_abs_gte)) return false;
  if (w.handicap_abs_lte != null && !dec.lte(hAbs, w.handicap_abs_lte)) return false;
  if (w.totals_gte != null && !dec.gte(tLine, w.totals_gte)) return false;
  if (w.totals_lte != null && !dec.lte(tLine, w.totals_lte)) return false;
  if (w.favorite_need != null && favNeed !== w.favorite_need) return false;
  if (w.favorite_need_in != null && !w.favorite_need_in.includes(favNeed)) return false;
  if (w.underdog_need != null && undNeed !== w.underdog_need) return false;
  if (w.underdog_need_in != null && !w.underdog_need_in.includes(undNeed)) return false;
  if (w.either_need_in != null && !(w.either_need_in.includes(favNeed) || w.either_need_in.includes(undNeed))) return false;
  if (w.final_round != null && match.group_context.final_round !== w.final_round) return false;
  if (w.same_time_kickoff != null && match.group_context.same_time_kickoff !== w.same_time_kickoff) return false;
  return true;
}

function confidence(w, hAbs, tLine) {
  const pairs = [
    ["gte", w.handicap_abs_gte, hAbs],
    ["lte", w.handicap_abs_lte, hAbs],
    ["gte", w.totals_gte, tLine],
    ["lte", w.totals_lte, tLine],
  ];
  const margins = [];
  for (const [kind, bound, value] of pairs) {
    if (bound == null) continue;
    margins.push(kind === "gte" ? value - bound : bound - value);
  }
  if (!margins.length) return HIGH_CONF;
  const worst = Math.min(...margins);
  return worst >= 0.25 - 1e-9 ? HIGH_CONF : LOW_CONF;
}

/**
 * Classify a MatchInput into one of 7 scenarios + confidence + reasons.
 * @param {import('./types.js').MatchInput} match
 */
export function classify(match) {
  const hAbs = Math.abs(match.market.line);
  const tLine = match.totals.line;
  const [favNeed, undNeed] = favUnd(match);

  for (const rule of CLASSIFICATION_RULES) {
    if (ruleMatches(rule.when, hAbs, tLine, favNeed, undNeed, match)) {
      return {
        scenario: rule.scenario,
        confidence: confidence(rule.when, hAbs, tLine),
        reasons: [rule.reason],
      };
    }
  }
  return {
    scenario: "unclear_skip",
    confidence: 0,
    reasons: ["No classification rule matched — skip and revisit input."],
  };
}
