/* =====================================================================
 * examples.js — canonical example match inputs (mirror of
 * tigerline/examples/*.json). Kept inline so the app is a single-fetch
 * static asset — no fetch to /examples/*.json.
 * ===================================================================== */
"use strict";

export const EXAMPLES = {
  belgium_nz_two_goal: {
    match_id: "2026-wc-belgium-newzealand",
    kickoff_utc: "2026-06-22T22:00:00+00:00",
    home: "Belgium",
    away: "New Zealand",
    group_context: {
      standings_summary: "Belgium 3 pts, NZ 0 pts; NZ already eliminated, Belgium needs win for top seed.",
      final_round: false,
      same_time_kickoff: false,
    },
    team_need: { home: "neutral", away: "rotation" },
    market: { line: "-1.75", favorite: "home", home_price: "1.92", away_price: "1.90" },
    totals: { line: "3.0", over_price: "1.95", under_price: "1.87" },
    bankroll: "5000",
    risk_flags: [],
  },

  egypt_iran_pressure_under: {
    match_id: "2026-wc-egypt-iran",
    kickoff_utc: "2026-06-25T21:00:00+00:00",
    home: "Egypt",
    away: "Iran",
    group_context: {
      standings_summary: "Both 3 pts, only 1 slot into R16, GD dead-even. Final round, parallel kickoff.",
      final_round: true,
      same_time_kickoff: true,
    },
    team_need: { home: "must_win", away: "must_win" },
    market: { line: "-0.25", favorite: "home", home_price: "1.95", away_price: "1.87" },
    totals: { line: "2.0", over_price: "2.05", under_price: "1.80" },
    bankroll: "5000",
    risk_flags: [],
  },

  rotation_trap_skip: {
    match_id: "2026-wc-rotation-trap",
    kickoff_utc: "2026-06-26T19:00:00+00:00",
    home: "Brazil",
    away: "Panama",
    group_context: {
      standings_summary: "Brazil already qualified as group winner; final round, second XI expected.",
      final_round: true,
      same_time_kickoff: false,
    },
    team_need: { home: "rotation", away: "must_win" },
    market: { line: "-1.5", favorite: "home", home_price: "1.98", away_price: "1.85" },
    totals: { line: "2.5", over_price: "1.92", under_price: "1.90" },
    bankroll: "5000",
    risk_flags: ["favorite_already_qualified"],
  },
};

export const EXAMPLE_NAMES = Object.keys(EXAMPLES);
