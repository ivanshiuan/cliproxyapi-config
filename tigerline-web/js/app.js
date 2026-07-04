/* =====================================================================
 * app.js — wires the form to the JS decision engine.
 * Zero backend — everything runs in the browser.
 * ===================================================================== */
"use strict";

import { parseMatch } from "./types.js";
import { recommend } from "./recommender.js";
import { review } from "./review.js";
import { DISCLAIMER } from "./compliance.js";
import { EXAMPLES, EXAMPLE_NAMES } from "./examples.js";
import { fmtDec } from "./decimal.js";

const $ = (id) => document.getElementById(id);
let currentPlan = null;
let currentMatch = null;

function loadExamplesList() {
  const sel = $("example-select");
  for (const name of EXAMPLE_NAMES) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  }
}

function loadExample() {
  const name = $("example-select").value;
  if (!name || !EXAMPLES[name]) return;
  fillForm(EXAMPLES[name]);
  clearError();
}

function fillForm(m) {
  $("match_id").value = m.match_id;
  $("kickoff_utc").value = m.kickoff_utc;
  $("home").value = m.home;
  $("away").value = m.away;
  $("standings_summary").value = m.group_context.standings_summary;
  $("final_round").checked = !!m.group_context.final_round;
  $("same_time_kickoff").checked = !!m.group_context.same_time_kickoff;
  $("need_home").value = m.team_need.home;
  $("need_away").value = m.team_need.away;
  $("ah_line").value = m.market.line;
  $("ah_fav").value = m.market.favorite;
  $("ah_home_price").value = m.market.home_price;
  $("ah_away_price").value = m.market.away_price;
  $("tot_line").value = m.totals.line;
  $("tot_over").value = m.totals.over_price;
  $("tot_under").value = m.totals.under_price;
  $("bankroll").value = m.bankroll;
}

function readForm() {
  return {
    match_id: $("match_id").value.trim(),
    kickoff_utc: $("kickoff_utc").value.trim(),
    home: $("home").value.trim(),
    away: $("away").value.trim(),
    group_context: {
      standings_summary: $("standings_summary").value.trim(),
      final_round: $("final_round").checked,
      same_time_kickoff: $("same_time_kickoff").checked,
    },
    team_need: { home: $("need_home").value, away: $("need_away").value },
    market: {
      line: $("ah_line").value.trim(),
      favorite: $("ah_fav").value,
      home_price: $("ah_home_price").value.trim(),
      away_price: $("ah_away_price").value.trim(),
    },
    totals: {
      line: $("tot_line").value.trim(),
      over_price: $("tot_over").value.trim(),
      under_price: $("tot_under").value.trim(),
    },
    bankroll: $("bankroll").value.trim(),
    risk_flags: [],
  };
}

function showError(msg) {
  const el = $("error-panel");
  el.textContent = msg;
  el.style.display = "block";
  $("result").style.display = "none";
}

function clearError() {
  $("error-panel").style.display = "none";
}

function analyze() {
  clearError();
  try {
    const raw = readForm();
    const match = parseMatch(raw);
    const plan = recommend(match);
    currentPlan = plan;
    currentMatch = match;
    render(plan);
  } catch (e) {
    showError(e.message || String(e));
  }
}

function render(plan) {
  $("result").style.display = "block";
  const scen = $("scenario");
  scen.textContent = plan.scenario;
  scen.classList.toggle(
    "skip",
    plan.scenario === "rotation_trap" || plan.harness.adjustment === "skip",
  );

  $("confidence").textContent = `confidence ${plan.classification.confidence.toFixed(2)}`;
  const harness = $("harness");
  harness.textContent = `harness ${plan.harness.adjustment}`;
  harness.className = "badge " + (
    plan.harness.adjustment === "upgrade" ? "up"
    : plan.harness.adjustment === "skip" ? "down"
    : ""
  );

  renderList($("reasons"), plan.classification.reasons);

  const corr = plan.corridor.scores.map(([h, a]) => `${h}-${a}`).join(" · ");
  const prim = plan.corridor.primary
    ? ` (primary ${plan.corridor.primary[0]}-${plan.corridor.primary[1]})` : "";
  $("corridor").textContent = corr + prim || "—";

  renderLeg($("main-bet"), plan.main_bet);
  renderLegs($("secondary"), plan.secondary);
  renderLegs($("cs-legs"), plan.correct_score);
  renderList($("avoid"), plan.avoid);
  renderList($("reserve"), plan.reserve_live);
  $("disclaimer").textContent = DISCLAIMER;

  $("review-panel").style.display = "none";
  $("review-result").style.display = "none";
}

function renderLeg(el, leg) {
  if (!leg) { el.textContent = "— SKIP —"; return; }
  el.innerHTML = "";
  const box = document.createElement("div");
  box.className = "leg";
  const sel = document.createElement("div");
  sel.className = "sel";
  sel.textContent = leg.selection;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${leg.market} · ${leg.level} · ${leg.stake_amount} (${fmtDec(leg.stake_pct, 3)} of bankroll)`;
  box.appendChild(sel); box.appendChild(meta);
  el.appendChild(box);
}

function renderLegs(el, legs) {
  el.innerHTML = "";
  if (!legs || !legs.length) { el.textContent = "—"; return; }
  for (const leg of legs) {
    const box = document.createElement("div");
    box.className = "leg";
    const sel = document.createElement("div");
    sel.className = "sel";
    sel.textContent = leg.selection;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${leg.market} · ${leg.level} · ${leg.stake_amount}`;
    box.appendChild(sel); box.appendChild(meta);
    el.appendChild(box);
  }
}

function renderList(el, items) {
  el.innerHTML = "";
  if (!items || !items.length) {
    const li = document.createElement("li");
    li.textContent = "—";
    li.style.color = "var(--muted)";
    el.appendChild(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    el.appendChild(li);
  }
}

function submitReview() {
  if (!currentPlan || !currentMatch) return;
  const hg = parseInt($("hg").value, 10);
  const ag = parseInt($("ag").value, 10);
  if (Number.isNaN(hg) || Number.isNaN(ag)) { showError("請輸入雙方進球數"); return; }
  try {
    const r = review(currentMatch, currentPlan, hg, ag);
    renderReview(r);
    clearError();
  } catch (e) {
    showError(e.message || String(e));
  }
}

function renderReview(r) {
  $("review-result").style.display = "block";
  const kv = $("review-kv");
  kv.innerHTML = "";
  const rows = [
    ["Actual", `${r.actual_score[0]}-${r.actual_score[1]}`],
    ["Scenario", r.scenario_correct ? "✓ correct" : "✗ drifted"],
    ["Corridor", r.corridor_hit ? "✓ hit" : "✗ missed"],
    ["Main", r.main_result],
    ["Score", `${r.score}/100`],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    kv.appendChild(dt); kv.appendChild(dd);
  }
  const sug = $("review-suggestions");
  sug.innerHTML = "";
  for (const s of r.suggestions || []) {
    const li = document.createElement("li"); li.textContent = s; sug.appendChild(li);
  }
}

$("analyze-btn").addEventListener("click", analyze);
$("load-example").addEventListener("click", loadExample);
$("toggle-review").addEventListener("click", () => {
  const p = $("review-panel");
  p.style.display = p.style.display === "block" ? "none" : "block";
});
$("review-btn").addEventListener("click", submitReview);

loadExamplesList();
