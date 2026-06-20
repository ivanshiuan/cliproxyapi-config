/* =====================================================================
 * SPORTS INTELLIGENCE DESK — 上線前 10 輪復盤 QA
 * 用法：node qa.js
 * 通過 = 功能全線可用 + 數據準確；任一 FAIL = 不可上線。
 * ===================================================================== */
"use strict";

/* ---- DOM 最小 shim，讓 app.js（瀏覽器 IIFE）能在 node 載入 ---- */
const stub = { innerHTML: "", textContent: "", classList: { toggle() {} }, dataset: {} };
global.window = {};
global.document = {
  addEventListener() {}, querySelector() { return stub; }, querySelectorAll() { return []; },
};
global.window.scrollTo = () => {};

require("./js/data.js");
require("./js/history.js");
require("./js/engine.js");
require("./js/backtest.js");
require("./js/live.js");
require("./js/app.js");
const S = global.window.SID;

let PASS = 0, FAIL = 0;
const fails = [];
function ok(cond, msg) { if (cond) PASS++; else { FAIL++; fails.push(msg); } }
const near = (a, b, e) => Math.abs(a - b) <= (e == null ? 1e-6 : e);
const num = (x) => typeof x === "number" && isFinite(x);

/* ================= 10 輪 ================= */
function round1_modules() {
  ok(S, "SID 全域存在");
  ["teams", "matches", "players", "results", "meta"].forEach(k => ok(S[k], `data.${k} 存在`));
  ["analyzeMatch", "analyzePortfolio", "monteCarlo"].forEach(k => ok(typeof S[k] === "function", `engine.${k}`));
  ["runBacktest", "backtestSelfTest"].forEach(k => ok(typeof S[k] === "function", `backtest.${k}`));
  ok(typeof S.refresh === "function", "live.refresh");
  ok(S.history && typeof S.history.lookup === "function", "history.lookup");
  ok(S._test && typeof S._test.renderDesk === "function", "app._test 導出");
}

function round2_math() {
  const A = S.matches.map(m => S.analyzeMatch(m));
  for (const a of A) {
    ok(near(a.model.home + a.model.draw + a.model.away, 1, 1e-6), `1X2 正規化 ${a.home.code}`);
    let msum = 0; a.matrix.forEach(r => r.forEach(p => msum += p));
    ok(near(msum, 1, 1e-6), `比分矩陣正規化 ${a.home.code}`);
    ok(a.model.overs[2.5] >= 0 && a.model.overs[2.5] <= 1, `O2.5 範圍 ${a.home.code}`);
    ok(num(a.lamH) && num(a.lamA) && a.lamH > 0 && a.lamA > 0, `λ 有效 ${a.home.code}`);
    ok(a.model.overs[1.5] >= a.model.overs[2.5] && a.model.overs[2.5] >= a.model.overs[3.5], `大小分單調 ${a.home.code}`);
  }
  // Poisson sums to 1
  let ps = 0; for (let k = 0; k < 30; k++) ps += S.util.poisson(k, 1.7);
  ok(near(ps, 1, 1e-4), "Poisson 機率和=1");
}

function round3_montecarlo() {
  for (const m of S.matches) {
    const a = S.analyzeMatch(m);
    ok(near(a.mc.avgGoals, a.lamH + a.lamA, 0.12), `MC avgG≈解析 ${m.home}`);
    ok(near(a.mc.home, a.model.raw.home, 0.05), `MC 主勝≈原始模型 ${m.home}`);  // MC 為原始,model 為校準後
    ok(a.model.raw && near(a.model.raw.home + a.model.raw.draw + a.model.raw.away, 1, 1e-6), `原始1X2正規化 ${m.home}`);
    ok(a.mc.homeCI[0] <= a.mc.home && a.mc.home <= a.mc.homeCI[1], `MC 區間含點估計 ${m.home}`);
    ok(near(a.mc.home + a.mc.draw + a.mc.away, 1, 0.01), `MC 1X2 和≈1 ${m.home}`);
  }
}

function round4_backtest() {
  const st = S.backtestSelfTest();
  ok(st.ok, "Brier 自我驗證(0/2/0.667)");
  const A = S.matches.map(m => S.analyzeMatch(m));
  const fake = {}; fake[A[0].match.id] = { gh: 2, ga: 0 }; fake[A[1].match.id] = { gh: 1, ga: 1 };
  const bt = S.runBacktest(A, fake);
  ok(bt.settled === 2, "回測結算 2 場");
  ok(num(bt.avgBrier) && bt.avgBrier >= 0 && bt.avgBrier <= 2, "Brier 範圍");
  ok(num(bt.avgLogLoss) && bt.avgLogLoss >= 0, "LogLoss 非負");
  ok(bt.rows[0].pickHit === true, "西2-0 → 小2.5 命中判定");  // total 2 < 2.5
  ok(typeof bt.beatsBaseline === "boolean", "基準比較存在");
  ok(S.runBacktest(A, {}).settled === 0, "空結果不結算");
}

function round5_data() {
  for (const [code, t] of Object.entries(S.teams)) {
    ok(num(t.elo) && t.elo > 1200 && t.elo < 2300, `Elo 合理 ${code}`);
    ok(num(t.xg_for) && t.xg_for > 0 && num(t.xg_against) && t.xg_against > 0, `xG 正值 ${code}`);
    ok(Array.isArray(t.form10) && t.form10.length === 3, `form10 結構 ${code}`);
    ok(t.flag && t.name, `名稱/旗 ${code}`);
  }
  for (const m of S.matches) {
    ok(S.teams[m.home] && S.teams[m.away], `對戰隊存在 ${m.id}`);
    const o = m.odds;
    ok(o.home > 1 && o.draw > 1 && o.away > 1, `1X2 賠率>1 ${m.id}`);
    const over = 1 / o.home + 1 / o.draw + 1 / o.away;
    ok(over > 1 && over < 1.25, `抽水合理 ${m.id} (${((over - 1) * 100).toFixed(1)}%)`);
    ok(!isNaN(Date.parse(m.kickoff_utc)), `開賽時間有效 ${m.id}`);
  }
  // 查證事實：先前移除的未驗證傷病不該復活
  const uru = S.players.URU.find(p => p.name.includes("Araújo"));
  ok(uru && uru.status === "ok", "URU Araújo 無造假傷病(=ok)");
  ok(!S.players.EGY.some(p => p.name.includes("Elneny")), "EGY 已移除未驗證 Elneny");
  ok(S.players.EGY.some(p => p.name.includes("Salah")) && S.players.EGY.some(p => p.name.includes("Marmoush")), "EGY 含查證主力");
}

function round6_timezone() {
  const t = S._test.twTime("2026-06-21T16:00:00Z");   // 應為台灣 06/22 00:00
  ok(/06\/22/.test(t) && /00:00/.test(t), `UTC→台灣 轉換 (${t})`);
  const t2 = S._test.twTime("2026-06-22T01:00:00Z");  // 台灣 06/22 09:00
  ok(/09:00/.test(t2), `UTC→台灣 轉換2 (${t2})`);
}

function round7_ui() {
  S._test.compute();
  const views = {
    desk: S._test.renderDesk(), portfolio: S._test.renderPortfolio(),
    timeline: S._test.renderTimeline(), settings: S._test.renderSettings(),
  };
  for (const [k, html] of Object.entries(views)) {
    ok(typeof html === "string" && html.length > 200, `${k} 頁渲染非空`);
    ok(!/undefined|NaN|\[object/.test(html), `${k} 頁無 undefined/NaN`);
  }
  const A = S._test.getAnalyses();
  const mhtml = S._test.renderMatch(A[0].match.id);
  ok(mhtml.length > 500 && !/undefined|NaN/.test(mhtml), "單場頁渲染乾淨");
  const memo = S._test.buildMemo(A[0]);
  ok(memo.includes("Match Intelligence Memo") && !/undefined|NaN/.test(memo), "Memo 生成乾淨");
}

function round8_edge() {
  // 極端錯配
  const ext = JSON.parse(JSON.stringify(S.matches[0]));
  S.teams.__H = { ...S.teams.ESP, code: "__H", elo: 2300 };
  S.teams.__A = { ...S.teams.KSA, code: "__A", elo: 1200 };
  ext.home = "__H"; ext.away = "__A";
  const a = S.analyzeMatch(ext);
  ok(near(a.model.home + a.model.draw + a.model.away, 1, 1e-6), "極端錯配仍正規化");
  ok(num(a.lamH) && num(a.lamA), "極端錯配 λ 有效");
  // 缺球員資料
  const np = JSON.parse(JSON.stringify(S.matches[0])); np.home = "__H"; np.away = "__A";
  ok(!S.players.__H, "無球員資料隊");
  ok(num(a.eg.avail_h.attackMult), "缺球員時可用性乘子有效");
  // 小 λ 不卡死 + 機率有效
  const mc = S.monteCarlo(0.1, 0.1, 0.2, 2000);
  ok(near(mc.home + mc.draw + mc.away, 1, 0.02), "極小 λ MC 正常");
  delete S.teams.__H; delete S.teams.__A;
}

function round9_live() {
  return Promise.resolve().then(async () => {
    S.liveConfig = { provider: null, key: "" };
    const r1 = await S.refresh();
    ok(r1.ok === false && r1.live === false, "未設定 → 降級");
    S.liveConfig = { provider: "apifootball", key: "X", season: 2026 };
    const r2 = await S.refresh();
    ok(r2.ok === false, "無 fetch/網路 → 失敗但不崩");
    // 降級後核心仍可用
    const A = S.matches.map(m => S.analyzeMatch(m));
    ok(A.length === 4 && A[0].grade.grade, "降級後分析正常");

    // Live 傷病同步：mock fetch，驗證「只動 curated 名單、依姓氏比對、不造新球員」
    const snap = JSON.parse(JSON.stringify(S.players));     // 用後還原，免污染其餘輪
    const origFetch = global.fetch;
    global.fetch = async (u) => ({
      json: async () => {
        if (/\/injuries/.test(u)) return { response: [
          { team: { name: "Spain" }, player: { name: "Lamine Yamal", type: "Missing Fixture" } },  // → out
          { team: { name: "Egypt" }, player: { name: "Mohamed Salah", type: "Questionable" } },     // → doubt
          { team: { name: "Spain" }, player: { name: "Nobody Unknown", type: "Missing" } },          // 不在名單 → 略過
          { team: { name: "Mars" },  player: { name: "X Y", type: "Missing" } },                     // 無對映隊 → 略過
        ] };
        return { response: [] };   // fixtures 空
      },
    });
    S.liveConfig = { provider: "apifootball", key: "X", season: 2026 };
    const r3 = await S.refresh();
    ok(r3.ok === true && r3.injuriesApplied === 2, `傷病同步 2 筆 (${r3.injuriesApplied})`);
    ok(S.players.ESP.find(p => p.name.includes("Yamal")).status === "out", "Yamal → out");
    ok(S.players.EGY.find(p => p.name.includes("Salah")).status === "doubt", "Salah → doubt");
    ok(!S.players.ESP.some(p => p.name.includes("Nobody")), "未造出名單外球員");
    global.fetch = origFetch;
    S.players = snap;                                        // 還原 curated 狀態
    S.meta.live = false;
  });
}

function round10_e2e() {
  const A = S.matches.map(m => S.analyzeMatch(m));
  ok(A.length === S.matches.length, "全場分析");
  for (const a of A) ok(["S", "A", "B", "C", "X"].includes(a.grade.grade), `評級有效 ${a.home.code}`);
  const P = S.analyzePortfolio(A);
  ok(num(P.score) && P.score >= 0 && P.score <= 100, "組合分範圍");
  ok(P.verdict && P.ranked.length === A.length, "組合結論完整");
  ok(A.every(a => a.hist && a.hist.h2h), "每場有歷史");
  ok(A.every(a => a.mc && a.mc.homeCI), "每場有 MC 區間");
  ok(A.every(a => a.ens && near(a.ens.home + a.ens.draw + a.ens.away, 1, 1e-6)), "每場集成1X2正規化");
  ok(typeof S.ENS_W === "number" && S.ENS_W >= 0 && S.ENS_W <= 1, "集成權重有效");
  ok(A.every(a => a.tacScore && num(a.tacScore.score) && a.tacScore.score >= -1 && a.tacScore.score <= 1), "A4 戰術分範圍");
}

/* ================= 執行：全套跑 10 次（抓隨機性/穩定性） ================= */
(async () => {
  const ITER = parseInt(process.argv[2] || "10", 10);
  console.log(`\n===== 上線前復盤：全套 ${ITER} 次 =====\n`);
  for (let i = 1; i <= ITER; i++) {
    const before = FAIL;
    round1_modules(); round2_math(); round3_montecarlo(); round4_backtest();
    round5_data(); round6_timezone(); round7_ui(); round8_edge();
    await round9_live(); round10_e2e();
    if (ITER <= 20 || i % 50 === 0 || FAIL !== before)
      console.log(`第 ${String(i).padStart(3)} 輪：${FAIL === before ? "✅ 全過" : "❌ 新增失敗 " + (FAIL - before)}`);
  }
  console.log(`\n===== 總計 PASS ${PASS} / FAIL ${FAIL} =====`);
  if (FAIL) { console.log("\n不可上線，失敗項："); [...new Set(fails)].forEach(f => console.log("  ✗ " + f)); process.exit(1); }
  else console.log("\n🟢 功能全線可用、數據通過 — 允許上線");
})();
