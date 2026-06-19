/* =====================================================================
 * SPORTS INTELLIGENCE DESK — APP (UI / 路由 / memo 生成)
 * ===================================================================== */
(function (SID) {
  "use strict";
  const U = SID.util, $ = (s) => document.querySelector(s);
  let ANALYSES = [], PORT = null, VIEW = "desk", CUR = null;

  /* ---------- 台灣時間 ---------- */
  function twTime(iso) {
    const d = new Date(iso);
    const f = new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", weekday: "short", hour12: false,
    });
    return f.format(d);
  }
  function hoursUntil(iso) { return (new Date(iso) - new Date()) / 36e5; }

  /* ---------- 計算全部 ---------- */
  function compute() {
    ANALYSES = SID.matches.map((m) => SID.analyzeMatch(m));
    PORT = SID.analyzePortfolio(ANALYSES);
  }

  /* ---------- 小工具 ---------- */
  const P = (x) => U.pct(x).toFixed(1) + "%";
  const sign = (x) => (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
  function edgeCell(e) {
    const c = e >= 0.02 ? "edge-pos" : e < 0 ? "edge-neg" : "";
    return `<span class="${c}">${sign(e)}</span>`;
  }
  function confColor(c) { return c >= 75 ? "var(--green)" : c >= 60 ? "var(--amber)" : "var(--red)"; }

  /* ===================================================================
   *  主控台
   * ================================================================= */
  function renderDesk() {
    const alerts = [];
    for (const a of ANALYSES) {
      const nm = a.home.name + " vs " + a.away.name;
      if (a.eg.avail_h.notes.length || a.eg.avail_a.notes.length)
        alerts.push({ t: "warn", txt: `🩹 ${nm}：` + [...a.eg.avail_h.notes, ...a.eg.avail_a.notes].join("；") });
      if (a.eg.env.wetBulb >= 27)
        alerts.push({ t: "warn", txt: `🌡️ ${nm}：濕球溫度 ${a.eg.env.wetBulb.toFixed(1)}°C，高溫壓制節奏` });
      if (a.mkt.movement < -0.06)
        alerts.push({ t: "warn", txt: `📉 ${nm}：盤口反向移動（${a.match.odds.sharp}）` });
      if (a.grade.grade === "X")
        alerts.push({ t: "x", txt: `⛔ ${nm}：評級 X — 資訊衝突/風險偏高，建議不碰` });
    }
    const high = [...ANALYSES].filter(a => a.grade.maxEdge >= 0.03)
      .sort((x, y) => y.grade.maxEdge - x.grade.maxEdge);

    let h = `<div class="wrap">`;

    // Portfolio summary
    h += `<div class="card" style="border-color:rgba(77,163,255,.35)">
      <div class="row-between">
        <div><div class="section-title" style="margin:0">四場組合評分</div>
        <div style="font-size:34px;font-weight:800;font-family:var(--mono);color:${confColor(PORT.score)}">${PORT.score}<span style="font-size:14px;color:var(--dim)"> /100</span></div></div>
        <div style="text-align:right"><div class="pill ${PORT.score>=75?'g':PORT.score>=55?'a':'r'}">${PORT.verdict}</div>
        <div class="tiny" style="margin-top:6px">平均信心 ${PORT.avgConf}・相關懲罰 -${PORT.corr}</div></div>
      </div></div>`;

    // Alerts
    h += `<div class="section-title">情報警報 (${alerts.length})</div>`;
    if (!alerts.length) h += `<div class="alert ok">✅ 無重大警報</div>`;
    for (const al of alerts.slice(0, 8))
      h += `<div class="alert ${al.t === "x" ? "" : al.t}">${al.txt}</div>`;

    // High edge
    h += `<div class="section-title">高 Edge 標的（市場錯價）</div>`;
    if (!high.length) h += `<div class="muted" style="padding:0 4px 8px">目前無顯著 Edge（市場定價接近模型）</div>`;
    for (const a of high) {
      h += `<div class="card tap" data-id="${a.match.id}">
        <div class="row-between"><b>${a.home.flag} ${a.home.name} vs ${a.away.name} ${a.away.flag}</b>
        <span class="pill g">${a.grade.best.mkt} ${sign(a.grade.maxEdge)}</span></div>
        <div class="tiny" style="margin-top:5px">模型 ${P(a.grade.best.model)} ｜ 市場 ${P(a.grade.best.market)} ｜ 信心 ${a.grade.conf}</div>
      </div>`;
    }

    // All fixtures
    h += `<div class="section-title">今日賽程（台灣時間）</div>`;
    for (const a of ANALYSES) h += matchRow(a);

    h += `</div>`;
    return h;
  }

  function matchRow(a) {
    const hrs = hoursUntil(a.match.kickoff_utc);
    const kt = hrs > 0 ? `T-${hrs.toFixed(0)}h` : "已開賽";
    return `<div class="card tap mrow" data-id="${a.match.id}">
      <div class="teams">
        <div class="tt"><span class="flag">${a.home.flag}</span>${a.home.name}
          <span class="tiny" style="margin-left:auto">${a.lamH.toFixed(2)}</span></div>
        <div class="tt"><span class="flag">${a.away.flag}</span>${a.away.name}
          <span class="tiny" style="margin-left:auto">${a.lamA.toFixed(2)}</span></div>
        <div class="meta">🕐 ${twTime(a.match.kickoff_utc)} ・ ${kt} ・ ${a.match.group}組 ・ ${a.match.city}</div>
        <div style="margin-top:6px">
          <span class="pill ${a.grade.best.edge>=0.02?'g':''}">${a.grade.best.mkt}</span>
          <span class="pill">信心 ${a.grade.conf}</span>
          ${a.eg.env.wetBulb>=27?'<span class="pill a">🌡️高溫</span>':''}
        </div>
      </div>
      <div class="grade ${a.grade.grade}">${a.grade.grade}</div>
    </div>`;
  }

  /* ===================================================================
   *  單場投研頁
   * ================================================================= */
  function renderMatch(id) {
    const a = ANALYSES.find(x => x.match.id === id);
    if (!a) return renderDesk();
    const m = a.match, mo = a.model, mk = a.mkt, g = a.grade;

    let h = `<div class="wrap"><span class="back" data-nav="desk">‹ 返回主控台</span>`;

    // Header
    h += `<div class="card">
      <div class="row-between"><h2 style="font-size:18px">${a.home.flag} ${a.home.name} <span class="muted">vs</span> ${a.away.name} ${a.away.flag}</h2>
      <div class="grade ${g.grade}">${g.grade}</div></div>
      <div class="tiny" style="margin-top:6px">🏆 ${m.competition}・${m.group}組 ${m.stage}</div>
      <div class="tiny">🕐 ${twTime(m.kickoff_utc)}（台灣）・🏟️ ${m.stadium}, ${m.city}・👨‍⚖️ ${m.referee}</div>
    </div>`;

    // 投資摘要
    h += `<div class="card"><div class="section-title" style="margin-top:0">投資摘要</div>
      <div class="memo">
      <div class="kv"><b>主判斷</b><span>${g.best.mkt}</span></div>
      <div class="kv"><b>模型機率</b><span>${P(g.best.model)}</span></div>
      <div class="kv"><b>市場機率</b><span>${P(g.best.market)}</span></div>
      <div class="kv"><b>Edge (Alpha)</b><span class="${g.maxEdge>=0.02?'edge-pos':g.maxEdge<0?'edge-neg':''}">${sign(g.maxEdge)}</span></div>
      <div class="kv"><b>信心等級</b><span style="color:${confColor(g.conf)}">${g.conf} / 100</span></div>
      <div class="kv"><b>納入四場組合</b><span>${g.grade==='X'?'否（不碰）':g.grade==='C'?'觀望':g.grade==='B'?'低權重':'可以'}</span></div>
      </div>
      <div class="conf-meter"><i style="width:${g.conf}%;background:${confColor(g.conf)}"></i></div>
    </div>`;

    // 1X2
    h += `<div class="card"><div class="section-title" style="margin-top:0">勝平負（模型 vs 市場）</div>
      <div class="probbar">
        <span class="h" style="width:${U.pct(mo.home)}%">${Math.round(U.pct(mo.home))}</span>
        <span class="d" style="width:${U.pct(mo.draw)}%">${Math.round(U.pct(mo.draw))}</span>
        <span class="a" style="width:${U.pct(mo.away)}%">${Math.round(U.pct(mo.away))}</span>
      </div>
      <div class="lbl3"><span>主勝 ${a.home.name}</span><span>和</span><span>客勝 ${a.away.name}</span></div>`;

    // Edge table
    h += `<table class="tbl" style="margin-top:12px"><tr><th>市場</th><th>模型</th><th>盤口隱含</th><th>Edge</th></tr>`;
    for (const e of g.edges)
      h += `<tr><td>${e.mkt}</td><td>${P(e.model)}</td><td>${P(e.market)}</td><td>${edgeCell(e.edge)}</td></tr>`;
    h += `</table></div>`;

    // 模型輸出 KPI
    h += `<div class="card"><div class="section-title" style="margin-top:0">模型輸出</div>
      <div class="kpis">
        <div class="kpi"><div class="v">${a.lamH.toFixed(2)}</div><div class="k">${a.home.name} xG</div></div>
        <div class="kpi"><div class="v">${a.lamA.toFixed(2)}</div><div class="k">${a.away.name} xG</div></div>
        <div class="kpi"><div class="v">${(a.lamH+a.lamA).toFixed(2)}</div><div class="k">總進球期望</div></div>
        <div class="kpi"><div class="v">${P(mo.overs[2.5])}</div><div class="k">大 2.5</div></div>
        <div class="kpi"><div class="v">${P(mo.btts)}</div><div class="k">BTTS</div></div>
        <div class="kpi"><div class="v">${a.cor.total.toFixed(1)}</div><div class="k">總角球期望</div></div>
      </div></div>`;

    // 比分矩陣
    h += `<div class="card"><div class="section-title" style="margin-top:0">正確比分矩陣（熱力圖）</div>`;
    h += scoreHeat(a.matrix, a.home, a.away);
    h += `<div style="margin-top:10px"><span class="muted">核心比分：</span>`;
    for (const s of a.scoreClass.core) h += `<span class="pill b">${s.s} (${P(s.p)})</span>`;
    h += `</div>`;
    if (a.scoreClass.dark.length) {
      h += `<div style="margin-top:4px"><span class="muted">黑馬比分：</span>`;
      for (const s of a.scoreClass.dark) h += `<span class="pill a">${s.s} (${P(s.p)})</span>`;
      h += `</div>`;
    }
    h += `</div>`;

    // 戰術對位
    h += `<div class="card"><div class="section-title" style="margin-top:0">戰術對位</div>
      <div class="memo">
      <p class="muted">${a.home.name}：${a.home.style}</p>
      <p class="muted">${a.away.name}：${a.away.style}</p>
      <ul>` + a.tac.map(t => `<li>${t}</li>`).join("") + `</ul></div></div>`;

    // 市場盤口
    h += `<div class="card"><div class="section-title" style="margin-top:0">市場盤口分析</div>
      <div class="memo">
      <div class="kv"><b>初盤（主）</b><span>${m.odds.opening_home.toFixed(2)}</span></div>
      <div class="kv"><b>即時盤（主／和／客）</b><span>${m.odds.home} / ${m.odds.draw} / ${m.odds.away}</span></div>
      <div class="kv"><b>盤口移動</b><span style="color:${mk.movement>0?'var(--green)':'var(--red)'}">${mk.movement>0?'主隊被壓低':'主隊走高'} (${mk.movement.toFixed(2)})</span></div>
      <div class="kv"><b>抽水(1X2)</b><span>${(mk.overround1x2*100).toFixed(1)}%</span></div>
      <div class="kv"><b>資金訊號</b><span>${m.odds.sharp}</span></div>
      </div></div>`;

    // 風險清單
    h += `<div class="card"><div class="section-title" style="margin-top:0">風險清單（風險分 ${g.risk}）</div>`;
    const risks = [...g.riskNotes];
    [...a.eg.avail_h.notes, ...a.eg.avail_a.notes].forEach(n => risks.push("傷病：" + n));
    a.eg.env.notes.forEach(n => risks.push("環境：" + n));
    if (!risks.length) h += `<div class="alert ok">✅ 無顯著風險</div>`;
    for (const r of risks) h += `<div class="alert warn">⚠️ ${r}</div>`;
    h += `</div>`;

    // 最終結論
    h += `<div class="card" style="border-color:${confColor(g.conf)}33">
      <div class="section-title" style="margin-top:0">最終結論</div>
      <div class="memo">
      <div class="kv"><b>主判斷</b><span>${g.best.mkt}（${P(g.best.model)}）</span></div>
      <div class="kv"><b>大小分傾向</b><span>${mo.overs[2.5]>0.5?'大 2.5 略優':'小 2.5 略優'}</span></div>
      <div class="kv"><b>角球傾向</b><span>${a.cor.total>9.5?a.home.name+' 角球偏高':'角球偏低'}</span></div>
      <div class="kv"><b>爆冷風險</b><span>${mo.away>0.28||mo.draw>0.3?'中高':mo.away>0.15?'中':'低'}</span></div>
      <div class="kv"><b>信心分</b><span style="color:${confColor(g.conf)}">${g.conf} / 100</span></div>
      <div class="kv"><b>評級</b><span>${g.grade}</span></div>
      </div>
      <button class="btn" data-memo="${m.id}" style="margin-top:10px;width:100%">📄 產生完整投研 Memo</button>
      <div id="memo-out"></div>
    </div>`;

    h += `</div>`;
    return h;
  }

  function scoreHeat(M, home, away) {
    const N = 6; // 顯示 0~5
    let max = 0;
    for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) max = Math.max(max, M[i][j]);
    let h = `<div class="matrix" style="grid-template-columns:24px repeat(${N},1fr)">`;
    h += `<div class="cell hd"></div>`;
    for (let j = 0; j < N; j++) h += `<div class="cell hd">${j}</div>`;
    for (let i = 0; i < N; i++) {
      h += `<div class="cell hd">${i}</div>`;
      for (let j = 0; j < N; j++) {
        const p = M[i][j], inten = p / max;
        const bg = `rgba(29,216,130,${(inten * 0.85).toFixed(2)})`;
        h += `<div class="cell" style="background:${bg}">${(p * 100).toFixed(0)}</div>`;
      }
    }
    h += `</div><div class="tiny" style="margin-top:4px">↕ ${home.name} 進球　↔ ${away.name} 進球（格內=機率%）</div>`;
    return h;
  }

  /* ---------- Memo 文字 ---------- */
  function buildMemo(a) {
    const m = a.match, g = a.grade, mo = a.model, mk = a.mkt;
    const injuries = [...a.eg.avail_h.notes, ...a.eg.avail_a.notes];
    const L = [];
    L.push(`【Match Intelligence Memo】`);
    L.push(`比賽：${a.home.name} vs ${a.away.name}`);
    L.push(`時間：${twTime(m.kickoff_utc)}（台灣）　組別：${m.group}　場地：${m.stadium}, ${m.city}`);
    L.push(`天氣：${m.env.temp}°C / 濕度${m.env.humidity}% / 濕球${a.eg.env.wetBulb.toFixed(1)}°C　裁判：${m.referee}`);
    L.push(``);
    L.push(`一、投資摘要`);
    L.push(`　主結論：${g.best.mkt}　模型 ${P(g.best.model)} vs 市場 ${P(g.best.market)}　Edge ${sign(g.maxEdge)}`);
    L.push(`　信心等級：${g.conf}/100（${g.grade} 級）　納入四場：${g.grade==='X'?'否':g.grade==='C'?'觀望':g.grade==='B'?'低權重':'可以'}`);
    L.push(``);
    L.push(`二、球隊基本面`);
    L.push(`　${a.home.name}：FIFA #${a.home.fifa}　Elo ${a.home.elo}　近10場 ${a.home.form10.join('-')}`);
    L.push(`　${a.away.name}：FIFA #${a.away.fifa}　Elo ${a.away.elo}　近10場 ${a.away.form10.join('-')}`);
    L.push(`　Elo 差：${a.eg.eloDiff>0?'+':''}${a.eg.eloDiff}　期望淨勝球：${a.eg.sup>0?'+':''}${a.eg.sup.toFixed(2)}`);
    L.push(``);
    L.push(`三、球員資產負債表`);
    L.push(`　缺陣/存疑：${injuries.length?injuries.join('；'):'無重大缺陣'}`);
    L.push(``);
    L.push(`四、戰術對位`);
    a.tac.forEach(t => L.push(`　・${t}`));
    L.push(``);
    L.push(`五、環境與水土`);
    L.push(`　${a.eg.env.notes.length?a.eg.env.notes.join('；'):'無顯著環境壓力'}`);
    L.push(``);
    L.push(`六、模型輸出`);
    L.push(`　A勝 ${P(mo.home)}　和 ${P(mo.draw)}　B勝 ${P(mo.away)}`);
    L.push(`　大2.5 ${P(mo.overs[2.5])}　小2.5 ${P(1-mo.overs[2.5])}　BTTS ${P(mo.btts)}`);
    L.push(`　預期進球 ${a.lamH.toFixed(2)} - ${a.lamA.toFixed(2)}　總角球 ${a.cor.total.toFixed(1)}`);
    L.push(``);
    L.push(`七、正確比分矩陣`);
    L.push(`　核心：${a.scoreClass.core.map(s=>s.s+'('+P(s.p)+')').join('  ')}`);
    L.push(`　黑馬：${a.scoreClass.dark.map(s=>s.s+'('+P(s.p)+')').join('  ')||'—'}`);
    L.push(``);
    L.push(`八、市場盤口分析`);
    L.push(`　初盤主 ${m.odds.opening_home} → 即時 ${m.odds.home}（${mk.movement>0?'壓低':'走高'}）　抽水 ${(mk.overround1x2*100).toFixed(1)}%　訊號：${m.odds.sharp}`);
    L.push(``);
    L.push(`九、風險清單`);
    (g.riskNotes.length?g.riskNotes:['無顯著風險']).forEach(r => L.push(`　・${r}`));
    L.push(``);
    L.push(`十、最終結論`);
    L.push(`　主判斷：${g.best.mkt}　備選：${g.edges[1].mkt}（Edge ${sign(g.edges[1].edge)}）`);
    L.push(`　不碰：${g.grade==='X'?'本場整體':'高賠波膽勿重壓'}　信心 ${g.conf}　風險等級：${g.risk>=25?'高':g.risk>=15?'中':'低'}`);
    return L.join("\n");
  }

  /* ===================================================================
   *  四場組合頁
   * ================================================================= */
  function renderPortfolio() {
    let h = `<div class="wrap">`;
    h += `<div class="card" style="border-color:rgba(77,163,255,.35)">
      <div class="row-between">
        <div><div class="section-title" style="margin-top:0">PORTFOLIO SCORE</div>
        <div style="font-size:40px;font-weight:800;font-family:var(--mono);color:${confColor(PORT.score)}">${PORT.score}</div></div>
        <div class="pill ${PORT.score>=75?'g':PORT.score>=55?'a':'r'}" style="font-size:13px">${PORT.verdict}</div>
      </div>
      <table class="tbl" style="margin-top:10px">
        <tr><td>平均信心</td><td style="text-align:right">${PORT.avgConf}</td></tr>
        <tr><td>相關性懲罰</td><td style="text-align:right;color:var(--red)">-${PORT.corr}</td></tr>
        <tr><td>過熱懲罰</td><td style="text-align:right;color:var(--red)">-${PORT.heat}</td></tr>
        <tr><td>資訊不完整懲罰</td><td style="text-align:right;color:var(--red)">-${PORT.incomplete}</td></tr>
      </table></div>`;

    h += `<div class="section-title">相關性風險地圖</div>`;
    if (!PORT.corrNotes.length) h += `<div class="alert ok">✅ 四場相關性低，分散良好</div>`;
    for (const n of PORT.corrNotes) h += `<div class="alert warn">🔗 ${n}</div>`;

    h += `<div class="section-title">四場信心排序</div>`;
    let i = 1;
    for (const a of PORT.ranked) {
      const verdict = a.grade.grade === 'X' ? '該刪 ⛔' : a.grade.grade === 'C' ? '只可觀望' : a.grade.grade === 'B' ? '低權重' : '可當主場 ✓';
      h += `<div class="card tap" data-id="${a.match.id}">
        <div class="row-between">
          <div><b>${i}. ${a.home.flag} ${a.home.name} vs ${a.away.name} ${a.away.flag}</b>
          <div class="tiny" style="margin-top:4px">${a.grade.best.mkt}・Edge ${sign(a.grade.maxEdge)}・${verdict}</div></div>
          <div style="text-align:right">
            <div class="grade ${a.grade.grade}" style="width:36px;height:36px;font-size:17px;display:inline-flex">${a.grade.grade}</div>
            <div class="tiny" style="margin-top:3px;color:${confColor(a.grade.conf)}">信心 ${a.grade.conf}</div>
          </div>
        </div>
        <div class="conf-meter" style="margin-top:8px"><i style="width:${a.grade.conf}%;background:${confColor(a.grade.conf)}"></i></div>
      </div>`;
      i++;
    }

    // 最終組合輸出表
    h += `<div class="section-title">組合輸出</div><div class="card"><table class="tbl">
      <tr><th>場次</th><th>結論</th><th>信心</th><th>納入</th></tr>`;
    for (const a of ANALYSES) {
      const inc = a.grade.grade === 'X' ? '否' : a.grade.grade === 'C' ? '觀望' : a.grade.grade === 'B' ? '小權重' : '是';
      h += `<tr><td>${a.home.code}-${a.away.code}</td><td>${a.grade.best.mkt}</td>
        <td style="color:${confColor(a.grade.conf)}">${a.grade.conf}</td><td>${inc}</td></tr>`;
    }
    h += `</table></div></div>`;
    return h;
  }

  /* ===================================================================
   *  賽前時間軸
   * ================================================================= */
  function renderTimeline() {
    const stages = [
      ["T-72h", "建立初版基本面、抓賽程/場地/天氣/休息天數、標記高風險場", true],
      ["T-48h", "更新傷病、媒體訓練狀態、市場盤口，重算勝平負與大小分", true],
      ["T-24h", "確認記者會、教練暗示、可能先發，更新環境壓力與戰術對位", false],
      ["T-6h", "確認天氣、盤口大幅異動、社群官方消息，標記疑似輪換", false],
      ["T-90m", "抓正式先發、重算球員可用性與 xG、比分矩陣，確認是否取消結論", false],
      ["賽後", "記錄預測 vs 實際、算 Brier / Log Loss、檢查錯因、更新模型權重", false],
    ];
    // 依最近一場 kickoff 判斷目前處於哪個階段
    const nearest = Math.min(...ANALYSES.map(a => hoursUntil(a.match.kickoff_utc)).filter(x => x > -2));
    let h = `<div class="wrap"><div class="section-title" style="margin-top:0">賽前投研時間軸</div>
      <div class="card"><div class="muted" style="margin-bottom:6px">最近一場開賽倒數：<b style="color:var(--amber)">${nearest>0?'T-'+nearest.toFixed(0)+'h':'已開賽'}</b></div>
      <ul class="timeline">`;
    for (const [t, d, done] of stages) {
      h += `<li class="${done ? 'done' : ''}"><div class="t">${t} ${done ? '✓ 已完成' : '待辦'}</div><div>${d}</div></li>`;
    }
    h += `</ul></div>
    <div class="card"><div class="section-title" style="margin-top:0">回測欄位（賽後自動填）</div>
      <table class="tbl"><tr><th>場次</th><th>模型主判斷</th><th>實際</th><th>Brier</th></tr>`;
    for (const a of ANALYSES)
      h += `<tr><td>${a.home.code}-${a.away.code}</td><td>${a.grade.best.mkt}</td><td class="muted">待填</td><td class="muted">—</td></tr>`;
    h += `</table><div class="tiny" style="margin-top:8px">賽後把實際比分填入 data.js → 引擎自動計算 Brier / Log Loss 並校準權重</div></div></div>`;
    return h;
  }

  /* ===================================================================
   *  路由 / 事件
   * ================================================================= */
  function render() {
    const main = $("#main");
    if (VIEW === "desk") main.innerHTML = renderDesk();
    else if (VIEW === "match") main.innerHTML = renderMatch(CUR);
    else if (VIEW === "portfolio") main.innerHTML = renderPortfolio();
    else if (VIEW === "timeline") main.innerHTML = renderTimeline();
    document.querySelectorAll(".tabs button").forEach(b =>
      b.classList.toggle("active", b.dataset.tab === VIEW || (VIEW === "match" && b.dataset.tab === "desk")));
    window.scrollTo(0, 0);
  }

  function nav(view, id) { VIEW = view; CUR = id || CUR; render(); }

  document.addEventListener("click", (e) => {
    const card = e.target.closest("[data-id]");
    const navEl = e.target.closest("[data-nav]");
    const tab = e.target.closest("[data-tab]");
    const memoBtn = e.target.closest("[data-memo]");
    if (memoBtn) {
      const a = ANALYSES.find(x => x.match.id === memoBtn.dataset.memo);
      $("#memo-out").innerHTML = `<pre class="mono" style="white-space:pre-wrap;font-size:11.5px;
        background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:10px;
        overflow-x:auto">${buildMemo(a).replace(/</g,"&lt;")}</pre>`;
      return;
    }
    if (tab) { nav(tab.dataset.tab); return; }
    if (navEl) { nav(navEl.dataset.nav); return; }
    if (card) { nav("match", card.dataset.id); return; }
  });

  /* ---------- 啟動 ---------- */
  function boot() {
    $("#meta-line").textContent =
      `${SID.meta.source}・快照 ${SID.meta.snapshot}・${SID.matches.length} 場標的`;
    compute();
    render();
  }
  document.addEventListener("DOMContentLoaded", boot);

})(window.SID);
