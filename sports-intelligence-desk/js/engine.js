/* =====================================================================
 * SPORTS INTELLIGENCE DESK — 多因子投研模型引擎
 * ---------------------------------------------------------------------
 * 8 個子模型串成一條 pipeline，輸出每場的「真實機率 vs 市場價格 = Alpha」。
 *
 *  M1 Team Power      Elo + 近況 + 經驗 + 教練 → 整體戰力差
 *  M2 Expected Goals  attack/defense 乘子 + Elo supremacy → λA, λB
 *  M6 Availability    核心球員缺陣 → 調整 λ（先套用到 M2）
 *  M7 Environment     高溫/濕度/時差/移動 → 體能折損（套用到 M2 + 信心）
 *  M3 Score Matrix    Poisson + Dixon-Coles 低比分修正 → 9x9 機率矩陣
 *     ↳ 1X2 / 大小分 / BTTS / 正確比分 全由矩陣導出
 *  M4 Corners         打法 + 對手低位 + 落後追分 → 總角球期望
 *  M5 Tactical        風格對位 → 文字化突破口
 *  M8 Market          去除抽水的隱含機率 vs 模型機率 → Edge
 *     ↳ 評級 S/A/B/C/X、信心分、四場組合 Portfolio Score
 * ===================================================================== */
(function (SID) {
  "use strict";

  /* ---------- 數學工具 ---------- */
  function factorial(n) { let r = 1; for (let i = 2; i <= n; i++) r *= i; return r; }
  function poisson(k, lambda) {
    return Math.pow(lambda, k) * Math.exp(-lambda) / factorial(k);
  }
  function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
  function pct(x) { return Math.round(x * 1000) / 10; }   // 0.612 → 61.2

  /* Elo 期望勝率（含和局之前的 raw 期望分） */
  function eloExpected(eloA, eloB) {
    return 1 / (1 + Math.pow(10, (eloB - eloA) / 400));
  }

  /* ---------- M6 球員可用性 → λ 乘子 ---------- */
  function availability(teamCode) {
    const list = SID.players[teamCode] || [];
    let attackMult = 1.0, concedeMult = 1.0;
    const notes = [];
    for (const p of list) {
      if (p.status === "ok") continue;
      const sev = p.status === "out" ? 1.0 : 0.5;      // 帶傷/存疑 = 半折
      if (p.role === "ATT" || p.role === "MID") {
        attackMult -= p.importance * sev * 0.6;          // 核心進攻缺 → 進攻↓
        notes.push(`${p.name} ${p.status === "out" ? "缺陣" : "存疑"}（進攻 -${pct(p.importance * sev * 0.6)}%）`);
      } else if (p.role === "GK") {
        concedeMult += p.importance * sev * 0.8;         // 門將缺 → 失球↑
        notes.push(`${p.name} 門將${p.status === "out" ? "缺陣" : "存疑"}（失球 +${pct(p.importance * sev * 0.8)}%）`);
      } else if (p.role === "DEF") {
        concedeMult += p.importance * sev * 0.5;
        notes.push(`${p.name} 後防${p.status === "out" ? "缺陣" : "存疑"}（被攻 +${pct(p.importance * sev * 0.5)}%）`);
      }
    }
    return { attackMult: clamp(attackMult, 0.55, 1.05), concedeMult: clamp(concedeMult, 0.95, 1.6), notes };
  }

  /* ---------- M7 環境壓力 → 體能折損 ----------
   * 回傳對「主隊/客隊」各自的 λ 折損乘子 + 信心扣分 + 文字。 */
  function environment(env, home, away) {
    let homeMult = 1.0, awayMult = 1.0, confPenalty = 0;
    const notes = [];

    // 濕球溫度近似（高溫高濕壓制高壓打法）
    const wetBulb = env.temp - (1 - env.humidity / 100) * (env.temp - 14) * 0.6;
    if (wetBulb >= 27) {
      const heat = clamp((wetBulb - 27) / 12, 0, 0.18);
      // 高壓/高耗能球隊被扣更多（ppda 低 = 壓迫強）
      homeMult -= heat * (home.ppda < 10 ? 1.2 : 0.8);
      awayMult -= heat * (away.ppda < 10 ? 1.2 : 0.8);
      confPenalty += 6;
      notes.push(`濕球溫度 ${wetBulb.toFixed(1)}°C 偏高 → 下半場節奏與壓迫衰減，總進球向下修`);
    }
    if (!env.roof && env.temp >= 31) notes.push("露天高溫午/晚場，補水暫停影響節奏");

    // 時差與長途移動（主要打開局專注與下半場體能）
    if (env.tz_diff >= 8) { confPenalty += 3; notes.push(`跨時區 ${env.tz_diff}h → 開局專注度風險`); }
    if (env.travel_away >= 3000) { awayMult -= 0.04; notes.push(`客隊長途移動 ${env.travel_away}km → 下半場體能折損`); }
    if (env.travel_home >= 3000) { homeMult -= 0.04; }
    if (env.rest_home < 4) homeMult -= 0.03;
    if (env.rest_away < 4) awayMult -= 0.03;

    return {
      homeMult: clamp(homeMult, 0.8, 1.05),
      awayMult: clamp(awayMult, 0.8, 1.05),
      confPenalty, wetBulb, notes,
    };
  }

  /* ---------- M1 + M2 期望進球 ----------
   * λ 由兩條訊號融合：
   *   (a) 攻防乘子 Poisson：本隊攻擊強度 × 對手防守弱度 × 賽事基準
   *   (b) Elo supremacy：整體戰力差轉成進球差，補強 xG 對強隊的低估
   * 再乘上近況、經驗、教練、可用性、環境。 */
  function expectedGoals(m, home, away) {
    const base = SID.TOURNAMENT_AVG_XG;
    const avail_h = availability(home.code);
    const avail_a = availability(away.code);
    const envR = environment(m.env, home, away);

    // (a) 攻防乘子
    const atkH = home.xg_for / base, defA = away.xg_against / base;
    const atkA = away.xg_for / base, defH = home.xg_against / base;
    let lamH_xg = base * atkH * defA;
    let lamA_xg = base * atkA * defH;

    // (b) Elo supremacy → 期望淨勝球（每 100 Elo ≈ 0.28 球）
    const eloDiff = home.elo - away.elo;
    const sup = clamp(eloDiff / 100 * 0.28, -2.6, 2.6);
    const totalXg = lamH_xg + lamA_xg;
    let lamH_elo = totalXg / 2 + sup / 2;
    let lamA_elo = totalXg / 2 - sup / 2;

    // 融合（xG 0.5 / Elo 0.5），近況微調
    const formAdj = (t) => 1 + ((t.form10[0] * 3 + t.form10[1]) / 30 - 0.5) * 0.12;
    let lamH = (0.5 * lamH_xg + 0.5 * lamH_elo) * formAdj(home);
    let lamA = (0.5 * lamA_xg + 0.5 * lamA_elo) * formAdj(away);

    // 經驗 + 教練（大賽臨場）微幅修正
    lamH *= 1 + (home.wc_exp - 60) / 1000 + (home.coach - 65) / 1500;
    lamA *= 1 + (away.wc_exp - 60) / 1000 + (away.coach - 65) / 1500;

    // M6 可用性
    lamH *= avail_h.attackMult * avail_a.concedeMult;
    lamA *= avail_a.attackMult * avail_h.concedeMult;

    // M7 環境
    lamH *= envR.homeMult;
    lamA *= envR.awayMult;

    lamH = clamp(lamH, 0.18, 4.2);
    lamA = clamp(lamA, 0.18, 4.2);

    return { lamH, lamA, avail_h, avail_a, env: envR, eloDiff, sup };
  }

  /* ---------- M3 比分矩陣（Poisson + Dixon-Coles 低比分修正） ---------- */
  const MAXG = 8;
  function dcTau(i, j, lh, la, rho) {
    if (i === 0 && j === 0) return 1 - lh * la * rho;
    if (i === 0 && j === 1) return 1 + lh * rho;
    if (i === 1 && j === 0) return 1 + la * rho;
    if (i === 1 && j === 1) return 1 - rho;
    return 1;
  }
  function scoreMatrix(lamH, lamA) {
    const rho = -0.06;   // 修正獨立 Poisson 對和局/低比分的低估
    const M = [];
    let sum = 0;
    for (let i = 0; i <= MAXG; i++) {
      M[i] = [];
      for (let j = 0; j <= MAXG; j++) {
        const p = poisson(i, lamH) * poisson(j, lamA) * dcTau(i, j, lamH, lamA, rho);
        M[i][j] = Math.max(p, 0);
        sum += M[i][j];
      }
    }
    for (let i = 0; i <= MAXG; i++)
      for (let j = 0; j <= MAXG; j++) M[i][j] /= sum;   // 正規化
    return M;
  }

  /* 校準係數：temperature scaling，修正 Poisson 對熱門勝率的壓縮。
     由大規模回測 (backtest_sim.js) 樣本內外擬合所得 γ≈1.25（兩種子一致）。 */
  const CALIB_GAMMA = 1.25;
  SID.ENS_W = SID.ENS_W != null ? SID.ENS_W : 0.4;   // A5 集成：模型權重（其餘給市場）
  function calibrate1x2(home, draw, away) {
    const h = Math.pow(home, CALIB_GAMMA), d = Math.pow(draw, CALIB_GAMMA), a = Math.pow(away, CALIB_GAMMA);
    const s = h + d + a || 1;
    return { home: h / s, draw: d / s, away: a / s };
  }

  /* 由矩陣導出所有市場 */
  function deriveMarkets(M) {
    let home = 0, draw = 0, away = 0, btts = 0;
    const overs = { 1.5: 0, 2.5: 0, 3.5: 0 };
    const scores = [];
    for (let i = 0; i <= MAXG; i++) for (let j = 0; j <= MAXG; j++) {
      const p = M[i][j];
      if (i > j) home += p; else if (i === j) draw += p; else away += p;
      if (i >= 1 && j >= 1) btts += p;
      const tot = i + j;
      if (tot > 1.5) overs[1.5] += p;
      if (tot > 2.5) overs[2.5] += p;
      if (tot > 3.5) overs[3.5] += p;
      scores.push({ s: `${i}:${j}`, i, j, p });
    }
    scores.sort((a, b) => b.p - a.p);
    const raw = { home, draw, away };
    const cal = calibrate1x2(home, draw, away);   // 校準後的 1X2（供評級/Edge/顯示）
    return { home: cal.home, draw: cal.draw, away: cal.away, raw, btts, overs, scores };
  }

  /* ---------- M4 角球模型 ---------- */
  function corners(m, home, away, mk) {
    // 基礎 = 雙方場均，依對手低位防守、控球壓制、落後追分傾向調整
    let cH = home.corners_for, cA = away.corners_for;
    // 強隊壓著打 → 角球增加；對手越弱越低位
    const pressH = clamp((home.elo - away.elo) / 300, -1, 1.4);
    cH += pressH * 1.5 + (home.directness > 40 ? 0.6 : 0);
    cA += -pressH * 1.0;
    cH = clamp(cH, 2, 11); cA = clamp(cA, 2, 11);
    const total = cH + cA;
    return { home: cH, away: cA, total, line: 9.5, overProb: clamp((total - 9.5) / 6 + 0.5, 0.1, 0.9) };
  }

  /* ---------- M5 戰術對位（文字化） ---------- */
  function tactical(home, away) {
    const out = [];
    if (home.ppda < 10 && away.directness < 35 && away.possession < 50)
      out.push(`${home.name}高壓 vs ${away.name}出球弱 → 前場逼搶易製造機會`);
    if (away.ppda > 13 && home.possession > 55)
      out.push(`${home.name}控球 vs ${away.name}低位鐵桶 → 角球與外圍射門多、轉化難`);
    if (Math.abs(home.elo - away.elo) > 300)
      out.push(`戰力差顯著（Elo ${home.elo - away.elo > 0 ? "+" : ""}${home.elo - away.elo}）→ 比賽走向一面倒風險`);
    if (home.set_piece_xg > 0.3 || away.set_piece_xg > 0.3)
      out.push("定位球是潛在破局點（雙方至少一方定位球 xG 偏高）");
    if (out.length === 0) out.push("兩隊風格接近，勝負取決於臨場執行與個人能力");
    return out;
  }

  /* ---------- A4 戰術對位量化 ----------
   * 把 M5 的文字對位轉成數值：score∈[-1,1]（正=利主隊），magnitude=決定性。
   * 純資訊/評等用，不改動已校準的機率（避免重新校準）。 */
  function tacticalScore(home, away) {
    let s = 0; const drivers = [];
    const add = (v, txt) => { s += v; drivers.push(`${v > 0 ? "+" : ""}${v.toFixed(2)} ${txt}`); };
    // 高壓 vs 弱出球
    if (home.ppda < 10 && away.directness < 35) add(0.18, `${home.name}高壓壓制${away.name}出球`);
    if (away.ppda < 10 && home.directness < 35) add(-0.18, `${away.name}高壓壓制${home.name}出球`);
    // 控球 vs 低位（控球方稍利但易膠著）
    if (home.possession - away.possession > 12) add(0.10, `${home.name}控球壓制`);
    if (away.possession - home.possession > 12) add(-0.10, `${away.name}控球壓制`);
    // 定位球威脅差
    add(clamp((home.set_piece_xg - away.set_piece_xg) * 0.8, -0.12, 0.12), "定位球威脅差");
    // 直接性 vs 高位（快反打高防線）
    if (home.directness > 42 && away.ppda < 10) add(0.10, `${home.name}快反克${away.name}高防線`);
    if (away.directness > 42 && home.ppda < 10) add(-0.10, `${away.name}快反克${home.name}高防線`);
    s = clamp(s, -1, 1);
    return { score: s, magnitude: Math.abs(s), favors: s > 0.05 ? home.name : s < -0.05 ? away.name : "中性", drivers };
  }

  /* ---------- M8 市場：去抽水隱含機率 + Edge ---------- */
  function marketImplied(odds) {
    const raw = { home: 1 / odds.home, draw: 1 / odds.draw, away: 1 / odds.away };
    const over = 1 / odds.over25, under = 1 / odds.under25;
    const s1x2 = raw.home + raw.draw + raw.away;
    const sou = over + under;
    return {
      home: raw.home / s1x2, draw: raw.draw / s1x2, away: raw.away / s1x2,
      over25: over / sou, under25: under / sou,
      overround1x2: s1x2 - 1,
      movement: odds.opening_home - odds.home,  // >0 = 主隊賠率被壓低（買盤湧入）
      sharp: odds.sharp,
    };
  }

  /* ---------- 評級 + 信心分 ---------- */
  function gradeMatch(model, mkt, eg, m) {
    // 三大 Edge
    const edges = [
      { mkt: "主勝", model: model.home, market: mkt.home, edge: model.home - mkt.home },
      { mkt: "和局", model: model.draw, market: mkt.draw, edge: model.draw - mkt.draw },
      { mkt: "客勝", model: model.away, market: mkt.away, edge: model.away - mkt.away },
      { mkt: "大 2.5", model: model.overs[2.5], market: mkt.over25, edge: model.overs[2.5] - mkt.over25 },
      { mkt: "小 2.5", model: 1 - model.overs[2.5], market: mkt.under25, edge: (1 - model.overs[2.5]) - mkt.under25 },
    ];
    edges.sort((a, b) => b.edge - a.edge);
    const best = edges[0];
    const maxEdge = best.edge;
    const conviction = Math.max(model.home, model.draw, model.away);

    // 風險：球員缺、環境壓力、盤口反向、戰力過懸殊（陷阱熱門）
    let risk = 0;
    const riskNotes = [];
    if (eg.avail_h.notes.length || eg.avail_a.notes.length) { risk += 12; riskNotes.push("核心球員缺陣/存疑"); }
    risk += eg.env.confPenalty;
    if (eg.env.confPenalty) riskNotes.push("環境體能壓力");
    if (mkt.movement < -0.06) { risk += 8; riskNotes.push("盤口反向移動（賠率走高）"); }
    if (conviction > 0.78 && maxEdge < 0.02) { risk += 10; riskNotes.push("熱門過熱、無價差（陷阱）"); }
    if (best.mkt === "和局") { risk += 6; riskNotes.push("主結論為和局，方差高"); }

    // 信心分 0~100
    let conf = 50 + maxEdge * 220 + (conviction - 0.4) * 40 - risk;
    conf = Math.round(clamp(conf, 5, 97));

    // 評級
    let grade;
    const agree = (best.edge > 0.03 && best.model > mkt[gradeKey(best.mkt)] && risk < 18);
    if (maxEdge >= 0.06 && risk <= 12 && conviction >= 0.5) grade = "S";
    else if (maxEdge >= 0.035 && risk <= 20) grade = "A";
    else if (maxEdge >= 0.02) grade = "B";
    else if (risk >= 25 || maxEdge < 0.0) grade = "X";
    else grade = "C";
    if (risk >= 30) grade = "X";

    return { edges, best, maxEdge, conviction, risk, riskNotes, conf, grade };
  }
  function gradeKey(label) {
    return { "主勝": "home", "和局": "draw", "客勝": "away", "大 2.5": "over25", "小 2.5": "under25" }[label];
  }

  /* ---------- 比分分層（核心 / 黑馬 / 陷阱） ---------- */
  function classifyScores(scores, mkt) {
    const core = scores.slice(0, 3);
    // 黑馬：機率中等(2~6%)但賠率高、模型相對市場有支撐
    const dark = scores.filter(s => s.p >= 0.025 && s.p <= 0.06).slice(0, 2);
    return { core, dark };
  }

  /* ---------- A2 Monte Carlo（參數不確定性模擬） ----------
   * 不只從固定 λ 抽比分（那只會重現解析解），而是先對 λ 加入不確定性
   * （反映「賽前資訊不完整、樣本小」），每次迭代抽一組 (λH,λA) 再抽比分，
   * 聚合 n 次 → 得到誠實的機率「區間」而非單點。CV 越大代表越不確定。
   */
  function samplePoisson(lambda) {           // Knuth
    const L = Math.exp(-lambda); let k = 0, p = 1;
    do { k++; p *= Math.random(); } while (p > L);
    return k - 1;
  }
  function randNorm() {                       // Box-Muller
    let u = 0, v = 0;
    while (u === 0) u = Math.random();
    while (v === 0) v = Math.random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  function quantile(sorted, q) {
    const i = clamp(Math.floor(q * (sorted.length - 1)), 0, sorted.length - 1);
    return sorted[i];
  }
  SID.monteCarlo = function (lamH, lamA, cv, n) {
    cv = cv || 0.18;            // λ 不確定度（資料越糊 → 越大）
    n = n || 10000;
    let h = 0, d = 0, a = 0, btts = 0, o15 = 0, o25 = 0, o35 = 0, totG = 0;
    const homeWinBoot = [];     // 分批估計，產生勝率信賴區間
    const batch = 500; let bh = 0, bc = 0;
    const drift = cv * cv / 2;   // mean-preserving：使 E[λ·e^(Nσ-σ²/2)] = λ，不偏離解析期望
    for (let it = 0; it < n; it++) {
      const lh = lamH * Math.exp(randNorm() * cv - drift);
      const la = lamA * Math.exp(randNorm() * cv - drift);
      const gi = samplePoisson(lh), gj = samplePoisson(la);
      if (gi > gj) { h++; bh++; } else if (gi === gj) d++; else a++;
      if (gi >= 1 && gj >= 1) btts++;
      const t = gi + gj;
      if (t > 1.5) o15++; if (t > 2.5) o25++; if (t > 3.5) o35++;
      totG += t;
      if (++bc === batch) { homeWinBoot.push(bh / batch); bh = 0; bc = 0; }
    }
    homeWinBoot.sort((x, y) => x - y);
    return {
      n, cv,
      home: h / n, draw: d / n, away: a / n, btts: btts / n,
      over: { 1.5: o15 / n, 2.5: o25 / n, 3.5: o35 / n },
      avgGoals: totG / n,
      homeCI: [quantile(homeWinBoot, 0.05), quantile(homeWinBoot, 0.95)],
    };
  };

  /* ---------- 單場完整分析 ---------- */
  SID.analyzeMatch = function (m, opts) {
    opts = opts || {};
    const home = SID.teams[m.home], away = SID.teams[m.away];
    const eg = expectedGoals(m, home, away);
    const M = scoreMatrix(eg.lamH, eg.lamA);
    const model = deriveMarkets(M);
    const mkt = marketImplied(m.odds);
    const grade = gradeMatch(model, mkt, eg, m);
    const cor = corners(m, home, away, model);
    const tac = tactical(home, away);
    const tacScore = tacticalScore(home, away);
    const scoreClass = classifyScores(model.scores, mkt);

    // A2：資料不確定度 → 缺資料/有傷病/環境壓力時 CV 加大（誠實放寬區間）
    let cv = 0.16;
    if (eg.avail_h.notes.length || eg.avail_a.notes.length) cv += 0.04;
    if (eg.env.confPenalty) cv += 0.03;
    if (home.wc_exp < 40 || away.wc_exp < 40) cv += 0.03;   // 黑馬/小樣本
    const mc = opts.mc === false ? null : SID.monteCarlo(eg.lamH, eg.lamA, cv, opts.n || 10000);

    // 歷史交手/大賽戰績（若 history.js 載入）
    const hist = SID.history ? SID.history.lookup(m, home, away) : null;

    // A5 Ensemble：模型(校準後) + 市場 加權 = 最佳「真實機率」估計。
    // 權重 ENS_W 由大規模回測樣本內外擬合（w_model≈0.4，兩種子一致，集成 Brier 低於模型與市場）。
    const w = SID.ENS_W;
    const ens = {
      home: w * model.home + (1 - w) * mkt.home,
      draw: w * model.draw + (1 - w) * mkt.draw,
      away: w * model.away + (1 - w) * mkt.away,
    };
    const es = ens.home + ens.draw + ens.away || 1;
    ens.home /= es; ens.draw /= es; ens.away /= es;
    ens.over25 = w * model.overs[2.5] + (1 - w) * mkt.over25;

    return {
      match: m, home, away, eg, model, mkt, grade, cor, tac, tacScore, scoreClass, matrix: M, mc, hist, ens,
      lamH: eg.lamH, lamA: eg.lamA,
    };
  };


  /* ---------- 四場組合評分（Portfolio Construction） ---------- */
  SID.analyzePortfolio = function (analyses) {
    const avgConf = analyses.reduce((s, a) => s + a.grade.conf, 0) / analyses.length;

    // 相關性懲罰：同日、同組、全押同一方向、全熱門
    let corr = 0; const corrNotes = [];
    const days = new Set(analyses.map(a => a.match.kickoff_utc.slice(0, 10)));
    if (days.size === 1) { corr += 8; corrNotes.push("四場同日 → 天氣/裁判尺度/節奏共振風險"); }
    const groups = analyses.map(a => a.match.group);
    if (new Set(groups).size < groups.length) { corr += 5; corrNotes.push("含同組賽事 → 動機/算分互相牽動"); }
    const picks = analyses.map(a => a.grade.best.mkt);
    const favPicks = analyses.filter(a => a.grade.best.mkt === "主勝" || a.grade.best.mkt === "客勝").length;
    if (favPicks >= 3) { corr += 7; corrNotes.push("多數押勝負方向 → 黑天鵝日集體受傷"); }
    const overPicks = analyses.filter(a => a.grade.best.mkt === "大 2.5").length;
    const underPicks = analyses.filter(a => a.grade.best.mkt === "小 2.5").length;
    if (overPicks >= 3 || underPicks >= 3) { corr += 6; corrNotes.push("大小分方向集中 → 裁判/節奏系統性錯"); }

    const heat = analyses.filter(a => a.grade.riskNotes.includes("熱門過熱、無價差（陷阱）")).length * 4;
    const incomplete = analyses.filter(a => a.grade.risk >= 20).length * 3;
    const liveRisk = analyses.filter(a => a.eg.avail_h.notes.length || a.eg.avail_a.notes.length).length * 2;

    let score = Math.round(clamp(avgConf - corr - heat - incomplete - liveRisk, 0, 100));

    let verdict;
    if (score >= 85) verdict = "可形成主組合";
    else if (score >= 75) verdict = "可用，但需降低權重";
    else if (score >= 65) verdict = "僅參考，不宜重壓";
    else if (score >= 55) verdict = "不建議組合";
    else verdict = "放棄";

    // 排序：信心高、可納入優先
    const ranked = [...analyses].sort((a, b) => b.grade.conf - a.grade.conf);

    return { score, verdict, avgConf: Math.round(avgConf), corr, corrNotes, heat, incomplete, ranked };
  };

  /* 工具導出給畫面用 */
  SID.util = { pct, poisson, eloExpected };

})(window.SID);
