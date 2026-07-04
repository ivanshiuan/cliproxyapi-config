/* =====================================================================
 * app.js — 表單 ↔ 決策引擎 ↔ 中文化 render。零後端。
 * ===================================================================== */
"use strict";

import { parseMatch } from "./types.js";
import { recommend } from "./recommender.js";
import { review } from "./review.js";
import { DISCLAIMER } from "./compliance.js";
import { EXAMPLES, EXAMPLE_NAMES } from "./examples.js";
import { fmtDec } from "./decimal.js";
import { tScenario, tHarness, tMainResult, tMarket } from "./i18n.js";

const $ = (id) => document.getElementById(id);
let currentPlan = null;
let currentMatch = null;

// 中文顯示名（給範例下拉用）
const EXAMPLE_LABELS = {
  belgium_nz_two_goal: "比利時 vs 紐西蘭（兩球落點盤）",
  egypt_iran_pressure_under: "埃及 vs 伊朗（末輪雙方需贏、小分）",
  rotation_trap_skip: "巴西 vs 巴拿馬（強隊輪休陷阱 → 跳過）",
};

function loadExamplesList() {
  const sel = $("example-select");
  for (const name of EXAMPLE_NAMES) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = EXAMPLE_LABELS[name] || name;
    sel.appendChild(opt);
  }
  // Default the dropdown to the belgium example so "載入" without touching
  // the dropdown does the intuitive thing.
  sel.value = "belgium_nz_two_goal";
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
  el.textContent = "⚠️ " + msg;
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
    // 分析完自動捲到結果區
    document.getElementById("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    showError(translateError(e.message || String(e)));
  }
}

function translateError(en) {
  if (/bare float rejected/.test(en)) return "盤口 / 賠率 / 本金請用文字（例如 \"-1.75\"），不要用純數字。";
  if (/StrictDecimal/.test(en)) return "有欄位不是有效數字，請檢查盤口與賠率。";
  if (/bankroll must be > 0/.test(en)) return "本金要大於 0。";
  if (/prices must be > 1/.test(en)) return "所有賠率必須大於 1.0。";
  if (/totals\.line must be > 0/.test(en)) return "大小盤口必須大於 0。";
  if (/kickoff_utc/.test(en)) return "開賽時間格式錯誤，應是 ISO-8601（例：2026-06-22T22:00:00+00:00）。";
  if (/match_id: must be lowercase/.test(en)) return "比賽識別碼只能用小寫英文、數字、連字號。";
  if (/required/.test(en)) return "有欄位沒填：" + en;
  return en;
}

function render(plan) {
  $("result").style.display = "block";

  const scen = $("scenario");
  scen.textContent = tScenario(plan.scenario);
  scen.classList.toggle(
    "skip",
    plan.scenario === "rotation_trap" || plan.harness.adjustment === "skip",
  );

  $("confidence").textContent = `信心 ${plan.classification.confidence.toFixed(2)}`;
  const harness = $("harness");
  harness.textContent = "門檻：" + tHarness(plan.harness.adjustment);
  harness.className = "badge " + (
    plan.harness.adjustment === "upgrade" ? "up"
    : plan.harness.adjustment === "skip" ? "down"
    : ""
  );

  renderList($("reasons"), plan.classification.reasons);

  const corrStrs = plan.corridor.scores.map(([h, a]) => `${h}-${a}`);
  const prim = plan.corridor.primary
    ? `（主推 ${plan.corridor.primary[0]}-${plan.corridor.primary[1]}）` : "";
  $("corridor").textContent = corrStrs.length ? corrStrs.join(" · ") + prim : "—";

  renderLeg($("main-bet"), plan.main_bet, true);
  renderLegs($("secondary"), plan.secondary);
  renderLegs($("cs-legs"), plan.correct_score);
  renderList($("avoid"), plan.avoid);
  renderList($("reserve"), plan.reserve_live);
  $("disclaimer").textContent = DISCLAIMER;

  $("review-panel").style.display = "none";
  $("review-result").style.display = "none";
}

function renderLeg(el, leg, big = false) {
  if (!leg) {
    el.innerHTML = `<div class="skip-note">— 這場跳過、不下 —</div>`;
    return;
  }
  el.innerHTML = "";
  const box = document.createElement("div");
  box.className = "leg" + (big ? " big" : "");
  const sel = document.createElement("div");
  sel.className = "sel";
  sel.textContent = leg.selection;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${tMarket(leg.market)} · 等級 ${leg.level} · 下 ${leg.stake_amount}（本金 ${fmtDec(leg.stake_pct * 100, 1)}%）`;
  box.appendChild(sel); box.appendChild(meta);
  el.appendChild(box);
}

function renderLegs(el, legs) {
  el.innerHTML = "";
  if (!legs || !legs.length) { el.innerHTML = `<div class="none">—</div>`; return; }
  for (const leg of legs) {
    const box = document.createElement("div");
    box.className = "leg";
    const sel = document.createElement("div");
    sel.className = "sel";
    sel.textContent = leg.selection;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${tMarket(leg.market)} · 等級 ${leg.level} · 下 ${leg.stake_amount}`;
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
    showError(translateError(e.message || String(e)));
  }
}

function renderReview(r) {
  $("review-result").style.display = "block";
  const kv = $("review-kv");
  kv.innerHTML = "";
  const rows = [
    ["實際比分", `${r.actual_score[0]}-${r.actual_score[1]}`],
    ["場型判斷", r.scenario_correct ? "✅ 正確" : "❌ 判錯"],
    ["比分走廊", r.corridor_hit ? "✅ 命中" : "❌ 沒中"],
    ["主單結果", tMainResult(r.main_result)],
    ["綜合分數", `${r.score} / 100`],
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
  document.getElementById("review-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("analyze-btn").addEventListener("click", analyze);
$("load-example").addEventListener("click", loadExample);
$("toggle-review").addEventListener("click", () => {
  const p = $("review-panel");
  p.style.display = p.style.display === "block" ? "none" : "block";
  if (p.style.display === "block") p.scrollIntoView({ behavior: "smooth", block: "start" });
});
$("review-btn").addEventListener("click", submitReview);

// 一打開就把 Belgium 範例載入表單 — 讓使用者立刻看到欄位長怎樣、按分析就有結果
loadExamplesList();
fillForm(EXAMPLES.belgium_nz_two_goal);
