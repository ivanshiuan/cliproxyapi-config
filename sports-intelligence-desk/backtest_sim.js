/* =====================================================================
 * SPORTS INTELLIGENCE DESK — 大規模回測引擎（Monte-Carlo Backtest）
 * 用法：node backtest_sim.js [N]
 * ---------------------------------------------------------------------
 * 目的：量化「真實引擎」的機率校準度與 Edge 偵測有效性，跑數千場。
 *
 * 方法論（防自欺）：
 *  - DGP（真相）= log-linear 進球模型，與引擎內部公式「不同」→ 非循環。
 *  - 引擎只看到「真實強度 + 觀測雜訊」的評級 → 模擬現實資訊不完整。
 *  - 市場賠率 = 真實機率 + 抽水 + 熱門偏誤 + 雜訊 → 真實對手。
 *  - 對比基準：模型 vs 市場 vs 均勻(1/3)。Brier 越低越好。
 *
 * 誠實聲明：這驗證「引擎輸出是否校準、Edge 是否有資訊量」。
 *           真實世界 Alpha 仍須靠實戰賽後回測累積（backtest.js）。
 * ===================================================================== */
"use strict";
global.window = {};
require("./js/data.js"); require("./js/history.js");
require("./js/engine.js"); require("./js/backtest.js");
const S = global.window.SID;

/* 可重現亂數（種子固定 → 回測可複現） */
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
let rnd = mulberry32(20260620);
function randn() { let u = 0, v = 0; while (!u) u = rnd(); while (!v) v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function poiss(l) { const L = Math.exp(-l); let k = 0, p = 1; do { k++; p *= rnd(); } while (p > L); return k - 1; }
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

/* 真相機率（解析 Poisson，獨立，maxG=10）作為「上帝視角」基準 */
function trueProbs(lh, la) {
  const fact = (n) => { let r = 1; for (let i = 2; i <= n; i++) r *= i; return r; };
  const pm = (k, l) => Math.pow(l, k) * Math.exp(-l) / fact(k);
  let h = 0, d = 0, a = 0, o25 = 0;
  for (let i = 0; i <= 10; i++) for (let j = 0; j <= 10; j++) {
    const p = pm(i, lh) * pm(j, la);
    if (i > j) h += p; else if (i === j) d += p; else a += p;
    if (i + j > 2.5) o25 += p;
  }
  return { home: h, draw: d, away: a, over25: o25 };
}

/* 由真實機率造市場賠率：抽水 + 熱門-冷門偏誤 + 雜訊 */
function makeOdds(tp, margin, biasNoise) {
  // 熱門偏誤：壓低熱門賠率(高估熱門)、拉高冷門
  const bias = (p) => clamp(p + (p - 1 / 3) * 0.04 + randn() * biasNoise, 0.01, 0.97);
  let h = bias(tp.home), d = bias(tp.draw), a = bias(tp.away);
  const s = h + d + a; h /= s; d /= s; a /= s;
  const m = 1 + margin;
  const dec = (p) => clamp(1 / (p * m), 1.01, 200);
  let o = clamp(tp.over25 + randn() * biasNoise, 0.05, 0.95);
  return {
    home: dec(h), draw: dec(d), away: dec(a),
    over25: dec(o), under25: dec(1 - o),
    btts_yes: 2.0, btts_no: 1.8, opening_home: dec(h) * (1 + randn() * 0.03),
    sharp: "sim",
  };
}

function brier3(p, oc) { const y = { home: 0, draw: 0, away: 0 }; y[oc] = 1; return (p.home - y.home) ** 2 + (p.draw - y.draw) ** 2 + (p.away - y.away) ** 2; }
function ll3(p, oc) { return -Math.log(clamp(p[oc], 1e-9, 1)); }

const N = parseInt(process.argv[2] || "5000", 10);

/* 累計器 */
let bModel = 0, bMarket = 0, bUniform = 0, bTrue = 0;
let lModel = 0, lMarket = 0;
const relBins = Array.from({ length: 10 }, () => ({ sp: 0, hit: 0, n: 0 }));
// Edge 有效性：模型聲稱有 edge 的標的，是否真的比市場接近真相
let edgeN = 0, edgeModelCloser = 0, edgeHit = 0, edgeMarketHit = 0;
// 分級表現
const byGrade = {};
// 球隊物件模板
function teamFrom(strAtk, strDef, eloNoise) {
  const xgF = clamp(1.30 * Math.exp(strAtk) * (1 + randn() * 0.10), 0.4, 3.2);
  const xgA = clamp(1.30 * Math.exp(-strDef) * (1 + randn() * 0.10), 0.4, 3.2);
  return {
    name: "SIM", code: "SIM", flag: "⚽",
    fifa: 20, elo: clamp(1700 + (strAtk + strDef) * 220 + eloNoise, 1250, 2250),
    xg_for: xgF, xg_against: xgA, gf: xgF, ga: xgA,
    form10: [5, 3, 2], squad_value: 3, avg_age: 27, wc_exp: 60, coach: 65,
    possession: 52, ppda: 11, directness: 38, set_piece_xg: 0.28,
    corners_for: 5, corners_against: 4.5, style: "sim",
  };
}

const t0 = Date.now();
for (let it = 0; it < N; it++) {
  // 真實潛在強度
  const aH = randn() * 0.45, dH = randn() * 0.40;
  const aA = randn() * 0.45, dA = randn() * 0.40;
  // DGP：log-linear（與引擎公式不同），含中立場微弱主場
  const base = 1.32, home = 0.05;
  const trueLH = clamp(base * Math.exp(aH - dA + home), 0.15, 5);
  const trueLA = clamp(base * Math.exp(aA - dH), 0.15, 5);
  const tp = trueProbs(trueLH, trueLA);

  // 引擎看到的隊伍 = 真實強度 + 觀測雜訊
  S.teams.SIMH = teamFrom(aH, dH, randn() * 120);
  S.teams.SIMA = teamFrom(aA, dA, randn() * 120);
  S.teams.SIMH.code = "SIMH"; S.teams.SIMA.code = "SIMA";
  const odds = makeOdds(tp, 0.06, 0.03);
  const m = {
    id: "SIM" + it, competition: "sim", group: "X", stage: "sim",
    home: "SIMH", away: "SIMA", neutral: true, stadium: "s", city: "s",
    kickoff_utc: "2026-06-21T16:00:00Z", referee: "sim",
    env: { temp: 22, humidity: 55, altitude: 50, roof: true, rest_home: 4, rest_away: 4, travel_home: 500, travel_away: 500, tz_diff: 2 },
    odds,
  };
  const a = S.analyzeMatch(m, { mc: false });   // 關掉 MC 加速
  const mp = { home: a.model.home, draw: a.model.draw, away: a.model.away };
  const mk = { home: a.mkt.home, draw: a.mkt.draw, away: a.mkt.away };

  // 抽一場真實結果
  const gi = poiss(trueLH), gj = poiss(trueLA);
  const oc = gi > gj ? "home" : gi === gj ? "draw" : "away";

  bModel += brier3(mp, oc); bMarket += brier3(mk, oc);
  bUniform += brier3({ home: 1 / 3, draw: 1 / 3, away: 1 / 3 }, oc);
  bTrue += brier3({ home: tp.home, draw: tp.draw, away: tp.away }, oc);
  lModel += ll3(mp, oc); lMarket += ll3(mk, oc);

  // 校準分箱（用主勝機率）
  for (const key of ["home", "draw", "away"]) {
    const b = clamp(Math.floor(mp[key] * 10), 0, 9);
    relBins[b].sp += mp[key]; relBins[b].hit += oc === key ? 1 : 0; relBins[b].n++;
  }

  // Edge 有效性：模型最佳 1X2 edge > 3% 時
  const be = a.grade.best;
  if (["主勝", "和局", "客勝"].includes(be.mkt) && be.edge > 0.03) {
    const k = be.mkt === "主勝" ? "home" : be.mkt === "和局" ? "draw" : "away";
    edgeN++;
    // 模型 vs 市場 誰更接近真相機率
    if (Math.abs(mp[k] - tp[k]) < Math.abs(mk[k] - tp[k])) edgeModelCloser++;
    if (oc === k) edgeHit++;
    // 市場對同一結果的隱含機率對應命中（對照）
  }

  // 分級
  const g = a.grade.grade;
  byGrade[g] = byGrade[g] || { n: 0, brier: 0 };
  byGrade[g].n++; byGrade[g].brier += brier3(mp, oc);
}
const secs = ((Date.now() - t0) / 1000).toFixed(1);

console.log(`\n===== 大規模回測：${N} 場（種子固定可複現，耗時 ${secs}s）=====\n`);
console.log("平均 Brier（越低越準，理論下限=真相機率）：");
console.log(`  真相(上帝視角) : ${(bTrue / N).toFixed(4)}`);
console.log(`  模型 (engine)  : ${(bModel / N).toFixed(4)}`);
console.log(`  市場 (含抽水)  : ${(bMarket / N).toFixed(4)}`);
console.log(`  均勻亂猜       : ${(bUniform / N).toFixed(4)}`);
console.log(`\n平均 Log Loss：模型 ${(lModel / N).toFixed(4)} ｜ 市場 ${(lMarket / N).toFixed(4)}`);

const skill = (1 - (bModel / N) / (bUniform / N)) * 100;     // 相對均勻的資訊增益
const vsTrueGap = (bModel / N) - (bTrue / N);                 // 距理論下限差距
console.log(`\n資訊量(Brier Skill Score vs 均勻)：${skill.toFixed(1)}%`);
console.log(`距理論下限差距：${vsTrueGap.toFixed(4)}（越接近 0 越好；雜訊使其無法為 0）`);
console.log(`模型是否優於市場：${bModel < bMarket ? "✅ 是" : "❌ 否"}（差 ${((bMarket - bModel) / N).toFixed(4)}）`);

console.log(`\n校準可靠度（預測 X% 的事，實際發生 %）：`);
console.log("  區間      預測   實際   樣本");
for (let i = 0; i < 10; i++) { const b = relBins[i]; if (b.n > 30) console.log(`  ${(i * 10).toString().padStart(2)}-${i * 10 + 10}%   ${(b.sp / b.n * 100).toFixed(1).padStart(5)}  ${(b.hit / b.n * 100).toFixed(1).padStart(5)}  ${String(b.n).padStart(5)}`); }

console.log(`\nEdge 偵測有效性（模型聲稱 1X2 edge>3% 共 ${edgeN} 場）：`);
console.log(`  模型機率比市場更接近真相之比例：${(edgeModelCloser / edgeN * 100).toFixed(1)}%（>50% = Edge 有資訊量）`);
console.log(`  該方向實際命中率：${(edgeHit / edgeN * 100).toFixed(1)}%`);

console.log(`\n分級平均 Brier（應隨 S→X 遞增＝越高級越準）：`);
for (const g of ["S", "A", "B", "C", "X"]) if (byGrade[g]) console.log(`  ${g}: ${(byGrade[g].brier / byGrade[g].n).toFixed(4)}  (n=${byGrade[g].n})`);

console.log(`\n誠實聲明：本回測驗證「引擎機率校準度 + Edge 資訊量」（DGP 與引擎異構、含觀測雜訊、對手為含抽水市場）。`);
console.log(`真實世界 Alpha 仍須靠實戰賽後回測累積（SID.results → backtest.js）。`);

delete S.teams.SIMH; delete S.teams.SIMA;
