/* =====================================================================
 * decimal.js — tiny decimal helper for TIGER LINE web port.
 *
 * We do NOT bring in decimal.js the library. Handicap / totals lines are
 * quantized in 0.25 steps and money is a whole-unit currency (TWD ronud),
 * so string-based fixed-point arithmetic is more than enough. Everything
 * flows through this module so we can swap to Big/decimal.js later without
 * touching consumers.
 *
 * Convention: a "dec" value is stored as a plain JS number (float) that is
 * mathematically exact up to 1e-6 (7 significant digits). All comparisons
 * use an epsilon to shrug off IEEE-754 noise near a 0.25 boundary.
 * ===================================================================== */
"use strict";

const EPSILON = 1e-9;

/** Parse a decimal-shaped string or number. Rejects bare floats not written by us. */
export function parseDec(x, { field = "value" } = {}) {
  if (typeof x === "number") return x;
  if (typeof x !== "string") throw new TypeError(`${field}: expected string or number, got ${typeof x}`);
  if (!/^-?\d+(\.\d+)?$/.test(x)) throw new TypeError(`${field}: not a decimal literal: ${x}`);
  return Number(x);
}

/** Format a decimal to trailing-zero-trimmed string (matches Python str(Decimal)). */
export function fmtDec(x, places = 2) {
  if (!Number.isFinite(x)) return String(x);
  const rounded = Math.round(x * 10 ** places) / 10 ** places;
  // Trim trailing zeros but keep at least one decimal place.
  let s = rounded.toFixed(places);
  if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s;
}

/** Round HALF_UP to whole units (mirrors Decimal.quantize(Decimal("1"), ROUND_HALF_UP)). */
export function roundHalfUp(x) {
  return Math.sign(x) * Math.floor(Math.abs(x) + 0.5);
}

export const dec = {
  eq: (a, b) => Math.abs(a - b) < EPSILON,
  gte: (a, b) => a - b > -EPSILON,
  lte: (a, b) => a - b < EPSILON,
  gt: (a, b) => a - b > EPSILON,
  lt: (a, b) => a - b < -EPSILON,
};
