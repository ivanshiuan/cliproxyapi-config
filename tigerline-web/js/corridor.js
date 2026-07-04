/* =====================================================================
 * corridor.js — score corridor templates. Faithful port of
 * tigerline/corridor.py + config/corridor_templates.yaml.
 * Templates are written favorite-as-home; flip when the away side is fav.
 * ===================================================================== */
"use strict";

export const CORRIDOR_TEMPLATES = {
  two_goal_landing: { primary: [2, 0], scores: [[2, 0], [3, 0], [3, 1], [2, 1], [4, 1]] },
  must_win_pressure: { primary: [2, 0], scores: [[1, 0], [2, 0], [2, 1], [3, 1]] },
  pressure_under: { primary: [1, 1], scores: [[0, 0], [1, 0], [1, 1], [0, 1]] },
  goal_market: { primary: [2, 1], scores: [[2, 1], [2, 2], [1, 2], [3, 1], [1, 3]] },
  narrow_win: { primary: [1, 0], scores: [[1, 0], [2, 1], [2, 0], [1, 1]] },
  rotation_trap: { primary: [1, 1], scores: [[1, 1], [0, 1], [1, 2]] },
  unclear_skip: { primary: [1, 1], scores: [[1, 1]] },
};

const orient = ([h, a], flip) => (flip ? [a, h] : [h, a]);
const key = ([h, a]) => `${h}-${a}`;

/**
 * @param {string} scenario
 * @param {import('./types.js').MatchInput} match
 */
export function buildCorridor(scenario, match) {
  const template = CORRIDOR_TEMPLATES[scenario] || CORRIDOR_TEMPLATES.unclear_skip;
  const flip = match.market.favorite === "away";
  const primary = orient(template.primary, flip);

  const seen = new Set();
  const scores = [];
  for (const s of template.scores) {
    const oriented = orient(s, flip);
    const k = key(oriented);
    if (seen.has(k)) continue;
    seen.add(k);
    scores.push(oriented);
  }
  if (!seen.has(key(primary))) scores.unshift(primary);

  return { scores, primary };
}
