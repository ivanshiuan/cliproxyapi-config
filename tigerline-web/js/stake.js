/* =====================================================================
 * stake.js — level → bankroll %. Ports tigerline/stake.py +
 * config/stake_levels.yaml.
 * ===================================================================== */
"use strict";

import { dec, roundHalfUp } from "./decimal.js";

export const STAKE_BANDS = {
  "A+": { min_pct: 0.18, max_pct: 0.25, default_pct: 0.20 },
  A:    { min_pct: 0.12, max_pct: 0.18, default_pct: 0.15 },
  B:    { min_pct: 0.07, max_pct: 0.12, default_pct: 0.09 },
  C:    { min_pct: 0.03, max_pct: 0.06, default_pct: 0.04 },
  D:    { min_pct: 0,    max_pct: 0,    default_pct: 0    },
};

/**
 * @param {"A+"|"A"|"B"|"C"|"D"} level
 * @param {number} bankroll
 * @param {"upgrade"|"normal"|"downgrade"|"skip"} adjustment
 * @returns {{pct:number, amount:number}}
 */
export function allocate(level, bankroll, adjustment = "normal") {
  if (adjustment === "skip" || level === "D") return { pct: 0, amount: 0 };
  const band = STAKE_BANDS[level];
  const pct = adjustment === "upgrade" ? band.max_pct
           : adjustment === "downgrade" ? band.min_pct
           : band.default_pct;
  return { pct, amount: roundHalfUp(bankroll * pct) };
}

/**
 * @param {number} confidence
 * @param {"upgrade"|"normal"|"downgrade"|"skip"} adjustment
 * @returns {"A+"|"A"|"B"|"C"|"D"}
 */
export function pickLevel(confidence, adjustment) {
  if (adjustment === "skip") return "D";
  if (dec.gte(confidence, 0.85)) return adjustment === "upgrade" ? "A+" : "A";
  if (dec.gte(confidence, 0.55)) return adjustment === "upgrade" ? "A"  : "B";
  if (dec.gte(confidence, 0.35)) return adjustment === "upgrade" ? "B"  : "C";
  return adjustment === "upgrade" ? "C" : "D";
}
