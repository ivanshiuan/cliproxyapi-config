/* =====================================================================
 * types.js — MatchInput validation, mirroring Pydantic v2 `frozen=True`
 * + `StrictDecimal`. Every field a Python model has, plus the same
 * refusal of bare float literals on line/money fields.
 * ===================================================================== */
"use strict";

import { parseDec } from "./decimal.js";

const TEAM_NEEDS = new Set(["must_win", "draw_ok", "rotation", "neutral", "unknown"]);
const SIDES = new Set(["home", "away"]);

/**
 * @typedef {Object} MatchInput
 * @property {string} match_id
 * @property {string} kickoff_utc
 * @property {string} home
 * @property {string} away
 * @property {{standings_summary: string, final_round: boolean, same_time_kickoff: boolean}} group_context
 * @property {{home: string, away: string}} team_need
 * @property {{line: number, favorite: "home"|"away", home_price: number, away_price: number}} market
 * @property {{line: number, over_price: number, under_price: number}} totals
 * @property {number} bankroll
 * @property {string[]} risk_flags
 */

/** Reject raw float literals to match Python's StrictDecimal. */
function requireDecString(v, field) {
  if (typeof v === "number") {
    throw new TypeError(`${field}: bare float rejected (use string, e.g. "-1.75")`);
  }
  return parseDec(v, { field });
}

/**
 * Validate a plain JS object into a MatchInput. Throws on any structural
 * or type violation with a message that names the offending field.
 * @param {any} raw
 * @returns {MatchInput}
 */
export function parseMatch(raw) {
  if (raw == null || typeof raw !== "object") throw new TypeError("MatchInput: expected object");

  const need = (k, why = "required") => {
    if (!(k in raw)) throw new TypeError(`MatchInput.${k}: ${why}`);
    return raw[k];
  };

  const match_id = String(need("match_id"));
  if (!/^[a-z0-9\-]+$/.test(match_id)) throw new TypeError("match_id: must be lowercase kebab-case");

  const kickoff_utc = String(need("kickoff_utc"));
  if (Number.isNaN(Date.parse(kickoff_utc))) throw new TypeError("kickoff_utc: not a valid ISO-8601 timestamp");

  const home = String(need("home")).trim();
  const away = String(need("away")).trim();
  if (!home || !away) throw new TypeError("home/away: non-empty required");

  const gc = need("group_context");
  const group_context = {
    standings_summary: String(gc.standings_summary || ""),
    final_round: Boolean(gc.final_round),
    same_time_kickoff: Boolean(gc.same_time_kickoff),
  };

  const tn = need("team_need");
  if (!TEAM_NEEDS.has(tn.home)) throw new TypeError(`team_need.home: invalid (${tn.home})`);
  if (!TEAM_NEEDS.has(tn.away)) throw new TypeError(`team_need.away: invalid (${tn.away})`);
  const team_need = { home: tn.home, away: tn.away };

  const mk = need("market");
  if (!SIDES.has(mk.favorite)) throw new TypeError(`market.favorite: invalid (${mk.favorite})`);
  const market = {
    line: requireDecString(mk.line, "market.line"),
    favorite: mk.favorite,
    home_price: requireDecString(mk.home_price, "market.home_price"),
    away_price: requireDecString(mk.away_price, "market.away_price"),
  };
  if (market.home_price <= 1 || market.away_price <= 1) throw new TypeError("market prices must be > 1");

  const tt = need("totals");
  const totals = {
    line: requireDecString(tt.line, "totals.line"),
    over_price: requireDecString(tt.over_price, "totals.over_price"),
    under_price: requireDecString(tt.under_price, "totals.under_price"),
  };
  if (totals.line <= 0) throw new TypeError("totals.line must be > 0");
  if (totals.over_price <= 1 || totals.under_price <= 1) throw new TypeError("totals prices must be > 1");

  const bankroll = requireDecString(need("bankroll"), "bankroll");
  if (bankroll <= 0) throw new TypeError("bankroll must be > 0");

  const risk_flags = Array.isArray(raw.risk_flags) ? raw.risk_flags.map(String) : [];

  return Object.freeze({
    match_id,
    kickoff_utc,
    home,
    away,
    group_context: Object.freeze(group_context),
    team_need: Object.freeze(team_need),
    market: Object.freeze(market),
    totals: Object.freeze(totals),
    bankroll,
    risk_flags: Object.freeze(risk_flags),
  });
}
