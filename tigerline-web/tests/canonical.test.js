/* =====================================================================
 * canonical.test.js — parity check against the Python engine's canonical
 * reproductions. If any of these drift, the JS port is out of sync with
 * the CLI and must be re-audited before deploy.
 * ===================================================================== */
"use strict";

import test from "node:test";
import assert from "node:assert/strict";

import { parseMatch } from "../js/types.js";
import { classify } from "../js/classifier.js";
import { buildCorridor } from "../js/corridor.js";
import { evaluateHarness } from "../js/harness.js";
import { pickLevel, allocate } from "../js/stake.js";
import { recommend } from "../js/recommender.js";
import { review } from "../js/review.js";
import { checkText, enforce, DISCLAIMER_ZH } from "../js/compliance.js";
import { EXAMPLES } from "../js/examples.js";

// ─── Reproduction 1: Belgium 5:1 New Zealand → two_goal_landing ──────────
test("Belgium NZ classifies as two_goal_landing with HIGH confidence", () => {
  const m = parseMatch(EXAMPLES.belgium_nz_two_goal);
  const c = classify(m);
  assert.equal(c.scenario, "two_goal_landing");
  assert.ok(c.confidence >= 0.85, `confidence ${c.confidence} not HIGH`);
});

test("Belgium NZ harness upgrades", () => {
  const m = parseMatch(EXAMPLES.belgium_nz_two_goal);
  const c = classify(m);
  const v = evaluateHarness(m, c);
  assert.equal(v.adjustment, "upgrade");
});

test("Belgium NZ recommender picks Belgium -1.5 at A+", () => {
  const m = parseMatch(EXAMPLES.belgium_nz_two_goal);
  const plan = recommend(m);
  assert.equal(plan.scenario, "two_goal_landing");
  assert.ok(plan.main_bet);
  assert.equal(plan.main_bet.selection, "Belgium -1.5");
  assert.equal(plan.main_bet.level, "A+");
  assert.equal(plan.main_bet.stake_amount, 1250); // 5000 * 0.25
});

test("Belgium NZ 5-1 review: scenario correct, corridor miss, main win", () => {
  const m = parseMatch(EXAMPLES.belgium_nz_two_goal);
  const plan = recommend(m);
  const r = review(m, plan, 5, 1);
  assert.equal(r.scenario_correct, true);
  assert.equal(r.corridor_hit, false); // 5-1 not in [2,0],[3,0],[3,1],[2,1],[4,1]
  assert.equal(r.main_result, "win");
  assert.ok(r.score >= 70, `score ${r.score} too low`);
  assert.ok(r.suggestions.length > 0, "should suggest widening corridor");
});

// ─── Reproduction 2: Egypt 1:1 Iran → pressure_under ─────────────────────
test("Egypt Iran classifies as pressure_under", () => {
  const m = parseMatch(EXAMPLES.egypt_iran_pressure_under);
  const c = classify(m);
  assert.equal(c.scenario, "pressure_under");
});

test("Egypt Iran recommender picks Under 2 (totals)", () => {
  const m = parseMatch(EXAMPLES.egypt_iran_pressure_under);
  const plan = recommend(m);
  assert.equal(plan.scenario, "pressure_under");
  assert.equal(plan.main_bet.market, "totals");
  assert.ok(plan.main_bet.selection.toLowerCase().startsWith("under"));
});

test("Egypt Iran corridor includes (0,0), (1,0), (1,1), (0,1)", () => {
  const m = parseMatch(EXAMPLES.egypt_iran_pressure_under);
  const corr = buildCorridor("pressure_under", m);
  const strs = corr.scores.map(([h, a]) => `${h}-${a}`);
  for (const s of ["0-0", "1-0", "1-1", "0-1"]) assert.ok(strs.includes(s), `missing ${s} in ${strs}`);
});

test("Egypt Iran 1-1 review: corridor hit", () => {
  const m = parseMatch(EXAMPLES.egypt_iran_pressure_under);
  const plan = recommend(m);
  const r = review(m, plan, 1, 1);
  assert.equal(r.corridor_hit, true);
});

test("Egypt Iran 1-1 total=2 vs Under 2 → push (settlement guard)", () => {
  const m = parseMatch(EXAMPLES.egypt_iran_pressure_under);
  const plan = recommend(m);
  const r = review(m, plan, 1, 1);
  assert.equal(r.main_result, "push"); // 1+1=2, Under 2, diff=0
});

// ─── Reproduction 3: rotation_trap → skip ─────────────────────────────────
test("Rotation trap forces skip", () => {
  const m = parseMatch(EXAMPLES.rotation_trap_skip);
  const c = classify(m);
  assert.equal(c.scenario, "rotation_trap");
  const v = evaluateHarness(m, c);
  assert.equal(v.adjustment, "skip");
});

test("Rotation trap plan has main=null and non-empty avoid", () => {
  const m = parseMatch(EXAMPLES.rotation_trap_skip);
  const plan = recommend(m);
  assert.equal(plan.main_bet, null);
  assert.ok(plan.avoid.length > 0);
  assert.equal(plan.correct_score.length, 0);
});

// ─── Stake bands ─────────────────────────────────────────────────────────
test("pickLevel + allocate combinations", () => {
  assert.equal(pickLevel(0.9, "upgrade"), "A+");
  assert.equal(pickLevel(0.9, "normal"), "A");
  assert.equal(pickLevel(0.4, "downgrade"), "C"); // 0.4 → base=C, downgrade stays C
  assert.equal(pickLevel(0.2, "downgrade"), "D"); // below 0.35 band → D
  assert.equal(pickLevel(0, "skip"), "D");

  assert.deepEqual(allocate("A+", 10000, "upgrade"), { pct: 0.25, amount: 2500 });
  assert.deepEqual(allocate("A", 10000, "normal"), { pct: 0.15, amount: 1500 });
  assert.deepEqual(allocate("C", 10000, "normal"), { pct: 0.04, amount: 400 });
  assert.deepEqual(allocate("D", 10000, "normal"), { pct: 0, amount: 0 });
  assert.deepEqual(allocate("A+", 10000, "skip"), { pct: 0, amount: 0 });
});

// ─── StrictDecimal-like rejection ────────────────────────────────────────
test("parseMatch rejects bare float on market.line", () => {
  const bad = JSON.parse(JSON.stringify(EXAMPLES.belgium_nz_two_goal));
  bad.market.line = -1.75; // bare float, not string
  assert.throws(() => parseMatch(bad), /StrictDecimal|bare float rejected/);
});

test("parseMatch accepts stringy decimal", () => {
  const m = parseMatch(EXAMPLES.belgium_nz_two_goal);
  assert.equal(m.market.line, -1.75);
  assert.equal(m.bankroll, 5000);
});

// ─── Compliance ──────────────────────────────────────────────────────────
test("compliance blocks forbidden phrase", () => {
  assert.throws(() => enforce("看我這單保證獲利"), /compliance violations/);
  assert.throws(() => enforce("guaranteed win"), /compliance violations/);
});

test("compliance appends bilingual disclaimer once", () => {
  const clean = "測試報告";
  const stamped = enforce(clean);
  assert.ok(stamped.includes(DISCLAIMER_ZH));
  // Idempotent
  assert.equal(enforce(stamped), stamped);
});

test("checkText finds inline CJK forbidden phrases", () => {
  const hits = checkText("這場包贏的方案穩賺不賠");
  assert.ok(hits.includes("包贏"));
  assert.ok(hits.includes("穩賺"));
});
