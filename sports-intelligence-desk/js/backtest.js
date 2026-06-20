/* =====================================================================
 * SPORTS INTELLIGENCE DESK — 回測與校準（護城河核心）
 * ---------------------------------------------------------------------
 * 賽後把「模型機率 vs 實際結果」量化：
 *   Brier Score（多分類，越低越準）、Log Loss、可靠度圖（reliability）。
 * 這讓系統能「自我證明」是否真有 Alpha，而非自說自話。
 *
 * 用法：
 *   1) 賽後在 data.js 的 SID.results 填入實際比分
 *   2) SID.runBacktest(analyses) → 回傳每場分數 + 總結 + 校準曲線
 * ===================================================================== */
(function (SID) {
  "use strict";
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

  /* 多分類 Brier：Σ(p_i - y_i)^2，y 為 one-hot。範圍 0(完美)~2(最差) */
  function brierMulti(probs, outcome) {
    const y = { home: 0, draw: 0, away: 0 };
    y[outcome] = 1;
    return ["home", "draw", "away"].reduce((s, k) => s + Math.pow(probs[k] - y[k], 2), 0);
  }
  /* Log Loss（多分類）：-log(p_實際)，越低越好 */
  function logLoss(probs, outcome) {
    return -Math.log(clamp(probs[outcome], 1e-9, 1));
  }
  /* 二元 Brier（給大小分用） */
  function brierBinary(p, hit) { return Math.pow(p - (hit ? 1 : 0), 2); }

  /* 由實際比分判定 1X2 結果 */
  function outcomeOf(gh, ga) { return gh > ga ? "home" : gh === ga ? "draw" : "away"; }

  /* 可靠度圖：把所有「機率預測 vs 是否發生」分箱，看預測 70% 的事是否真的約 70% 發生 */
  function reliability(points, bins) {
    bins = bins || 5;
    const buckets = Array.from({ length: bins }, () => ({ sumP: 0, hit: 0, n: 0 }));
    for (const { p, hit } of points) {
      const b = clamp(Math.floor(p * bins), 0, bins - 1);
      buckets[b].sumP += p; buckets[b].hit += hit ? 1 : 0; buckets[b].n++;
    }
    return buckets.map((b, i) => ({
      range: `${(i / bins * 100).toFixed(0)}-${((i + 1) / bins * 100).toFixed(0)}%`,
      predicted: b.n ? b.sumP / b.n : null,
      actual: b.n ? b.hit / b.n : null,
      n: b.n,
    }));
  }

  /* 主流程：對有實際結果的場次結算 */
  SID.runBacktest = function (analyses, results) {
    results = results || SID.results || {};
    const rows = [];
    const relPoints = [];   // 蒐集所有 1X2 機率點做校準
    for (const a of analyses) {
      const r = results[a.match.id];
      if (!r || r.gh == null) continue;
      const oc = outcomeOf(r.gh, r.ga);
      const mo = a.model;
      const brier = brierMulti(mo, oc);
      const ll = logLoss(mo, oc);
      const total = r.gh + r.ga;
      const ouHit = total > 2.5;
      const ouBrier = brierBinary(mo.overs[2.5], ouHit);
      // 模型主判斷是否命中（方向）
      const pickHit = checkPick(a.grade.best, oc, total);
      rows.push({
        id: a.match.id, label: `${a.home.name}-${a.away.name}`,
        score: `${r.gh}:${r.ga}`, outcome: oc,
        pModel: { home: mo.home, draw: mo.draw, away: mo.away },
        brier, logLoss: ll, ouBrier, pick: a.grade.best.mkt, pickHit,
      });
      relPoints.push({ p: mo.home, hit: oc === "home" });
      relPoints.push({ p: mo.draw, hit: oc === "draw" });
      relPoints.push({ p: mo.away, hit: oc === "away" });
    }
    if (!rows.length) return { settled: 0, message: "尚無已結算場次（賽後填入 SID.results 即自動計算）", rows: [] };

    const avg = (k) => rows.reduce((s, r) => s + r[k], 0) / rows.length;
    const hitRate = rows.filter(r => r.pickHit).length / rows.length;
    // 基準：總是丟均勻 1/3 的 Brier = 0.667；越低於它越有資訊
    return {
      settled: rows.length,
      avgBrier: avg("brier"),
      avgLogLoss: avg("logLoss"),
      avgOuBrier: avg("ouBrier"),
      pickHitRate: hitRate,
      baselineBrier: 0.667,
      beatsBaseline: avg("brier") < 0.667,
      reliability: reliability(relPoints, 5),
      rows,
    };
  };

  function checkPick(best, oc, total) {
    switch (best.mkt) {
      case "主勝": return oc === "home";
      case "客勝": return oc === "away";
      case "和局": return oc === "draw";
      case "大 2.5": return total > 2.5;
      case "小 2.5": return total < 2.5;
      default: return null;
    }
  }

  /* 自我驗證：用已知例子確認數學正確（node 跑 backtest.js 時觸發） */
  SID.backtestSelfTest = function () {
    const perfect = brierMulti({ home: 1, draw: 0, away: 0 }, "home");      // 應為 0
    const worst = brierMulti({ home: 0, draw: 0, away: 1 }, "home");        // 應為 2
    const uniform = brierMulti({ home: 1 / 3, draw: 1 / 3, away: 1 / 3 }, "home"); // 0.667
    return {
      perfect: perfect.toFixed(4), worst: worst.toFixed(4), uniform: uniform.toFixed(4),
      ok: Math.abs(perfect) < 1e-9 && Math.abs(worst - 2) < 1e-9 && Math.abs(uniform - 0.6667) < 1e-3,
    };
  };

  SID.backtest = { brierMulti, logLoss, brierBinary, outcomeOf, reliability };
})(window.SID);
