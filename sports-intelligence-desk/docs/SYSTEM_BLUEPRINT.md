# SYSTEM BLUEPRINT — Sports Intelligence Desk (SID)
### 從零重建整套系統的完整規劃藍圖（可直接交付任意 AI Agent 執行）

> **這份文件的用途**：拿到它的 AI／工程師，**不需要任何原始碼或額外上下文**，
> 即可依本藍圖把整套「賽事投研量化系統」從零**設計 → 實作 → 測試 → 部署上線**。
> 所有子模型的**確切公式、常數、資料結構、模組契約、UI 規格、驗收標準**都寫在裡面。
>
> **產物定位**：純前端（vanilla JS，零執行期依賴）單頁應用，把每場球賽當「資產」，
> 輸出「真實機率 vs 市場隱含機率的落差（Edge/Alpha）」，並能自我回測證明是否有資訊量。

---

## 第 0 部分：總綱

### 0.1 產品哲學（務必內化，否則會做成普通預測器）
1. **不預測比分，量化錯價**：輸出的是「模型機率 − 市場去抽水機率 = Edge」，把比賽當待開盤資產。
2. **誠實優先於漂亮**：機率要校準、要能被回測打臉；寧可顯示「資訊不足、區間寬」也不假裝精準。
3. **不捏造事實**：傷病/先發/歷史交手只能來自查證快照或官方 API；無資料就標「無」，**嚴禁杜撰**。
4. **市場是強對手**：純模型常輸給含抽水的市場，故最終最佳估計用「模型 + 市場」集成。

### 0.2 技術約束（硬性）
| 項目 | 規格 |
|---|---|
| 前端 | 原生 HTML/CSS/JS，IIFE 模組掛 `window.SID`，**無框架、無打包器、無執行期依賴** |
| 建置/測試 | Node ≥ 18（僅用內建 `fs`/`path`，**零 npm 依賴**） |
| 金錢/機率 | 全程確定性；機率必正規化（Σ=1，誤差 <1e-6） |
| 時間 | 資料存 UTC ISO 字串；顯示轉 `Asia/Taipei`（用 `Intl.DateTimeFormat`） |
| 部署 | Cloudflare Pages（靜態）+ 可選 Cloudflare Worker（CORS 代理） |

### 0.3 模組拓撲
```
data.js ─┐
history.js ─┤
           ├─→ engine.js ──→ app.js (UI/路由/Memo)
live.js ─┘        │
                  └─→ backtest.js (賽後自我證明)
build.js → dist/        qa.js / backtest_sim.js (驗收)
```
全部模組皆 `(function(SID){ ... })(window.SID)` 形式，互相用 `SID.*` 取用，**載入順序固定**：
`data → history → engine → backtest → live → app`。

---

## 第 1 部分：資料層（data.js）

定義 `window.SID`，掛上以下資料庫。所有數值是「賽前情報快照」。

### 1.1 全域常數
```
SID.TOURNAMENT_AVG_XG = 1.30   // 賽事單隊場均 xG 基準
```

### 1.2 SID.teams（球隊基本面，key = 三碼代碼）
每隊欄位：
| 欄位 | 型別 | 意義 |
|---|---|---|
| name, code, flag | string | 名稱 / 三碼 / emoji 國旗 |
| fifa | int | FIFA 排名 |
| elo | number | Elo 評分（合理範圍 1200–2300） |
| xg_for, xg_against | number(>0) | 近 12 月加權場均 xG 攻 / 守 |
| gf, ga | number | 場均進/失球（顯示用） |
| form10 | [勝,平,負] | 近 10 場，三數和=10 |
| squad_value | number | 陣容身價（億歐，顯示用） |
| avg_age | number | 平均年齡 |
| wc_exp | 0–100 | 大賽經驗量化 |
| coach | 0–100 | 教練臨場量化 |
| possession | number | 控球% |
| ppda | number | 每防守動作對手傳球數（**越低=壓迫越強**） |
| directness | number | 直接性（越高=越快打身後） |
| set_piece_xg | number | 定位球 xG |
| corners_for, corners_against | number | 場均角球 |
| style | string | 文字風格描述 |

### 1.3 SID.players（球員資產，key = 隊代碼 → array）
只列影響模型的核心/缺陣球員：
```
{ name, role: "ATT"|"MID"|"GK"|"DEF", importance: 0~1, status: "ok"|"doubt"|"out" }
```
`importance` = 對球隊 xG 的影響權重。**未查證者不得列入**（無資料 = 不填）。

### 1.4 SID.matches（賽程 + 環境 + 盤口，array）
```
{
  id, competition, group, stage, home, away, neutral,
  stadium, city, country, kickoff_utc (ISO), referee,
  env: { temp(°C), humidity(%), altitude(m), roof(bool),
         rest_home, rest_away, travel_home(km), travel_away(km), tz_diff(h) },
  odds: { home, draw, away, over25, under25, btts_yes, btts_no,
          opening_home, sharp("home steam"等文字) }   // 十進位賠率，含抽水
}
```

### 1.5 SID.results（賽後實際比分，回測用，key = match.id）
```
{ "WC2026-H-ESP-KSA": { gh: 3, ga: 0 }, ... }   // 未填的場次回測自動略過
```

### 1.6 SID.meta
```
{ source: string, snapshot: "YYYY-MM-DD", live: false }
```

---

## 第 2 部分：歷史層（history.js）

### 2.1 SID.tournamentHistory（大賽底蘊，key = 隊代碼）
`{ titles, apps, best, knockout, pedigree(0~100), note? }`，pedigree = 世界盃底蘊量化。

### 2.2 SID.headToHead（歷史交手，key = 兩隊代碼字典序 join "_"）
`{ first: bool, summary: string, games: [{comp, score}] }`。**首次交手據實標 first:true，不杜撰。**

### 2.3 SID.history.lookup(m, home, away) → 回傳
`{ h2h, homePedigree, awayPedigree, notes[] }`。notes 規則：
- 兩隊都有 pedigree：底蘊差 |gap|≥35 標「懸殊（X 經驗壓制）」否則平鋪兩值；附 note。
- `h2h.first` 為真 → push「⚠️ 首次交手 → 無對位先驗，信心略降」。

---

## 第 3 部分：引擎（engine.js）— 系統核心，公式必須精確複製

### 3.0 數學工具
```
factorial(n)            // 迴圈
poisson(k, λ) = λ^k · e^(−λ) / k!
clamp(x, lo, hi)
pct(x) = round(x*1000)/10            // 0.612 → 61.2
eloExpected(a,b) = 1/(1+10^((b−a)/400))
```

### 3.1 M6 可用性 availability(teamCode) → {attackMult, concedeMult, notes[]}
對 `SID.players[teamCode]` 每個 `status≠"ok"` 的球員：
```
sev = (status==="out") ? 1.0 : 0.5
ATT 或 MID : attackMult  −= importance · sev · 0.6
GK         : concedeMult += importance · sev · 0.8
DEF        : concedeMult += importance · sev · 0.5
```
最後 `attackMult = clamp(.,0.55,1.05)`、`concedeMult = clamp(.,0.95,1.6)`。

### 3.2 M7 環境 environment(env, home, away) → {homeMult, awayMult, confPenalty, wetBulb, notes[]}
```
wetBulb = temp − (1 − humidity/100) · (temp − 14) · 0.6
if wetBulb ≥ 27:
    heat = clamp((wetBulb−27)/12, 0, 0.18)
    homeMult −= heat · (home.ppda<10 ? 1.2 : 0.8)      // 高壓球隊更耗能
    awayMult −= heat · (away.ppda<10 ? 1.2 : 0.8)
    confPenalty += 6
if tz_diff ≥ 8 : confPenalty += 3
if travel_away ≥ 3000 : awayMult −= 0.04
if travel_home ≥ 3000 : homeMult −= 0.04
if rest_home < 4 : homeMult −= 0.03
if rest_away < 4 : awayMult −= 0.03
homeMult = clamp(.,0.8,1.05) ; awayMult = clamp(.,0.8,1.05)
```

### 3.3 M1+M2 期望進球 expectedGoals(m, home, away) → {lamH, lamA, avail_h, avail_a, env, eloDiff, sup}
```
base = TOURNAMENT_AVG_XG (1.30)
// (a) 攻防乘子
atkH=home.xg_for/base ; defA=away.xg_against/base
atkA=away.xg_for/base ; defH=home.xg_against/base
lamH_xg = base · atkH · defA
lamA_xg = base · atkA · defH
// (b) Elo supremacy（每 100 Elo ≈ 0.28 球）
eloDiff = home.elo − away.elo
sup = clamp(eloDiff/100 · 0.28, −2.6, 2.6)
totalXg = lamH_xg + lamA_xg
lamH_elo = totalXg/2 + sup/2 ; lamA_elo = totalXg/2 − sup/2
// 融合 50/50 + 近況
formAdj(t) = 1 + ((t.form10[0]·3 + t.form10[1])/30 − 0.5)·0.12
lamH = (0.5·lamH_xg + 0.5·lamH_elo) · formAdj(home)
lamA = (0.5·lamA_xg + 0.5·lamA_elo) · formAdj(away)
// 經驗 + 教練
lamH *= 1 + (home.wc_exp−60)/1000 + (home.coach−65)/1500   // away 同理
// M6 可用性
lamH *= avail_h.attackMult · avail_a.concedeMult
lamA *= avail_a.attackMult · avail_h.concedeMult
// M7 環境
lamH *= env.homeMult ; lamA *= env.awayMult
lamH = clamp(.,0.18,4.2) ; lamA = clamp(.,0.18,4.2)
```

### 3.4 M3 比分矩陣 scoreMatrix(lamH, lamA) → 9×9（MAXG=8）
Dixon-Coles 低比分修正，`rho = −0.06`：
```
dcTau(i,j):
  (0,0) → 1 − lamH·lamA·rho
  (0,1) → 1 + lamH·rho
  (1,0) → 1 + lamA·rho
  (1,1) → 1 − rho
  其他  → 1
M[i][j] = max(poisson(i,lamH)·poisson(j,lamA)·dcTau(i,j), 0)，最後全體正規化(除以總和)
```

### 3.5 校準 + 集成常數
```
CALIB_GAMMA = 1.25      // 溫度縮放，修正 Poisson 對熱門勝率的壓縮（由大規模回測擬合）
SID.ENS_W   = 0.4       // 集成中「模型」權重，其餘給市場
calibrate1x2(h,d,a): 各取 ^γ 後正規化
```

### 3.6 由矩陣導出市場 deriveMarkets(M) → {home,draw,away, raw, btts, overs{1.5,2.5,3.5}, scores[]}
```
遍歷 M[i][j]：
  i>j → home ; i==j → draw ; i<j → away
  i≥1 且 j≥1 → btts
  (i+j) > 1.5/2.5/3.5 → 對應 overs 累加
  scores.push({s:`${i}:${j}`, i, j, p})
scores 依 p 由大到小排序
raw = {home,draw,away}（未校準）
cal = calibrate1x2(raw) → 回傳的 home/draw/away 用 cal（評級/顯示/集成都用校準後）
```

### 3.7 M4 角球 corners(m, home, away)
```
cH=home.corners_for ; cA=away.corners_for
pressH = clamp((home.elo−away.elo)/300, −1, 1.4)
cH += pressH·1.5 + (home.directness>40 ? 0.6 : 0)
cA += −pressH·1.0
cH=clamp(.,2,11) ; cA=clamp(.,2,11) ; total=cH+cA
line=9.5 ; overProb = clamp((total−9.5)/6 + 0.5, 0.1, 0.9)
```

### 3.8 M5 戰術（文字）tactical(home,away) → string[]
依規則 push 文字（高壓 vs 弱出球、控球 vs 鐵桶、戰力懸殊、定位球破局…），空則給保底句。

### 3.9 A4 戰術量化 tacticalScore(home,away) → {score∈[−1,1], magnitude, favors, drivers[]}
```
home.ppda<10 且 away.directness<35 : +0.18（高壓壓制弱出球）；對稱情形 −0.18
home.possession−away.possession >12 : +0.10；對稱 −0.10
+ clamp((home.set_piece_xg−away.set_piece_xg)·0.8, −0.12, 0.12)   // 定位球差
home.directness>42 且 away.ppda<10 : +0.10（快反克高防線）；對稱 −0.10
score=clamp(Σ,−1,1) ; favors = score>0.05?home : score<−0.05?away : "中性"
```
> **僅供資訊/評等，不回頭改動已校準機率。**

### 3.10 M8 市場 marketImplied(odds) → 去抽水隱含機率
```
raw = {1/home, 1/draw, 1/away} ; s1x2 = Σraw → home/draw/away = raw/s1x2
over=1/over25, under=1/under25, sou=over+under → over25/under25 = /sou
overround1x2 = s1x2 − 1
movement = opening_home − home   // >0 = 主隊被壓低（買盤湧入）
sharp = odds.sharp
```

### 3.11 評級 gradeMatch(model, mkt, eg, m) → {edges[], best, maxEdge, conviction, risk, riskNotes[], conf, grade}
```
edges = 5 項：主勝/和局/客勝(model.home/draw/away − mkt 對應)、
             大2.5(model.overs[2.5]−mkt.over25)、小2.5((1−overs2.5)−mkt.under25)
依 edge 由大到小排序；best=edges[0]；maxEdge=best.edge
conviction = max(model.home, model.draw, model.away)

risk = 0：
  有傷病 notes → +12（riskNotes「核心球員缺陣/存疑」）
  += eg.env.confPenalty（有值 → 「環境體能壓力」）
  mkt.movement < −0.06 → +8（「盤口反向移動」）
  conviction>0.78 且 maxEdge<0.02 → +10（「熱門過熱、無價差（陷阱）」）
  best.mkt==="和局" → +6（「主結論為和局，方差高」）

conf = round(clamp(50 + maxEdge·220 + (conviction−0.4)·40 − risk, 5, 97))

grade：
  maxEdge≥0.06 且 risk≤12 且 conviction≥0.5 → "S"
  否則 maxEdge≥0.035 且 risk≤20 → "A"
  否則 maxEdge≥0.02 → "B"
  否則 risk≥25 或 maxEdge<0 → "X"
  否則 → "C"
  最後 if risk≥30 → "X"（覆寫）
```

### 3.12 比分分層 classifyScores(scores) → {core: 前3, dark: p∈[0.025,0.06] 取前2}

### 3.13 A2 蒙地卡羅 SID.monteCarlo(lamH, lamA, cv=0.18, n=10000)
對 λ 加入不確定性後抽樣（誠實產生「區間」而非單點）：
```
drift = cv²/2                                  // mean-preserving
每次迭代：
  lh = lamH · exp(randNorm()·cv − drift)        // randNorm: Box-Muller
  la = lamA · exp(randNorm()·cv − drift)
  gi = samplePoisson(lh) ; gj = samplePoisson(la)   // samplePoisson: Knuth
  統計 home/draw/away、btts、over1.5/2.5/3.5、totG
  每 batch=500 次記一筆 home 勝率 → 排序後取 5%/95% 分位 = homeCI
回傳 { n, cv, home, draw, away, btts, over{}, avgGoals, homeCI:[lo,hi] }
```

### 3.14 單場彙整 SID.analyzeMatch(m, opts) → 完整物件
```
eg = expectedGoals ; M = scoreMatrix(eg.lamH,eg.lamA) ; model = deriveMarkets(M)
mkt = marketImplied(m.odds) ; grade = gradeMatch(model,mkt,eg,m)
cor = corners ; tac = tactical ; tacScore = tacticalScore ; scoreClass = classifyScores
// MC 的 cv 依資料品質放寬：
cv = 0.16 ; if 有傷病 cv+=0.04 ; if env.confPenalty cv+=0.03 ; if 任一隊 wc_exp<40 cv+=0.03
mc = (opts.mc===false) ? null : monteCarlo(eg.lamH, eg.lamA, cv, opts.n||10000)
hist = SID.history?.lookup(m,home,away)
// A5 集成（校準後模型 + 去抽水市場），w=SID.ENS_W
ens.home = w·model.home + (1−w)·mkt.home（draw/away 同理），再正規化
ens.over25 = w·model.overs[2.5] + (1−w)·mkt.over25
回傳 { match, home, away, eg, model, mkt, grade, cor, tac, tacScore,
       scoreClass, matrix:M, mc, hist, ens, lamH, lamA }
```

### 3.15 四場組合 SID.analyzePortfolio(analyses) → {score, verdict, avgConf, corr, corrNotes[], heat, incomplete, ranked[]}
```
avgConf = mean(grade.conf)
corr=0：
  四場同日(kickoff 前10字相同) → +8「天氣/裁判/節奏共振」
  含同組(group 有重複) → +5「動機/算分牽動」
  best.mkt 為主勝或客勝者 ≥3 → +7「黑天鵝日集體受傷」
  best.mkt==="大 2.5" ≥3 或 "小 2.5" ≥3 → +6「裁判/節奏系統性錯」
heat = count(riskNotes 含「熱門過熱…」)·4
incomplete = count(risk≥20)·3
liveRisk = count(有傷病 notes)·2
score = round(clamp(avgConf − corr − heat − incomplete − liveRisk, 0, 100))
verdict：≥85「可形成主組合」/≥75「可用，降權重」/≥65「僅參考」/≥55「不建議」/else「放棄」
ranked = analyses 依 grade.conf 由高到低
```

### 3.16 導出
`SID.util = { pct, poisson, eloExpected }`。

---

## 第 4 部分：回測（backtest.js）

```
brierMulti(probs, outcome) = Σ_{k∈home,draw,away} (probs[k] − y[k])²   // y one-hot；範圍 0~2
logLoss(probs, outcome) = −ln(clamp(probs[outcome], 1e-9, 1))
brierBinary(p, hit) = (p − (hit?1:0))²
outcomeOf(gh,ga) = gh>ga?"home":gh===ga?"draw":"away"
reliability(points, bins=5)：把 {p,hit} 依 floor(p·bins) 分箱，回 {range, predicted, actual, n}
```
`SID.runBacktest(analyses, results=SID.results)`：對有比分的場次算 brier/logLoss/ouBrier、
主判斷命中(checkPick)，彙總 `{settled, avgBrier, avgLogLoss, avgOuBrier, pickHitRate,
baselineBrier:0.667, beatsBaseline, reliability, rows[]}`；無場次回 `{settled:0,...}`。
`SID.backtestSelfTest()`：驗證 perfect=0 / worst=2 / uniform≈0.667。

---

## 第 5 部分：Live 對接（live.js）

`SID.liveConfig = { provider, key, season, proxy, date }`。三家 provider：
- **apifootball**：`/fixtures?league=1&season=` + **`/injuries?league=1&season=`**（傷病）
- **footballdata**：`/v4/competitions/WC/matches`
- **thesportsdb**：`/eventsday.php?d=<date>&s=Soccer`（date 預設今天）

`url(path)`：若有 proxy → `proxy + encodeURIComponent(path)`（解 CORS）。
名稱→代碼用 `NAME2CODE` 對映表。

`SID.refresh()`：
1. 無 provider/key → `{ok:false, live:false}`（降級用快照）。
2. 抓 fixtures，只取 home/away 都能對映 curated 的，更新既有 match 的 kickoff、賽後比分寫入 `SID.results`。
3. **傷病同步**（僅 apifootball 有 injuries）：抓回後**只比對 curated 名單既有球員（依姓氏小寫比對）**，命中才改 status；**不新增球員、不造假**；失敗靜默降級。回傳 `injuriesApplied`。
4. 成功 → 設 `meta.live=true`、`meta.source`、`meta.snapshot`，回 `{ok:true, fetched, usable, injuriesApplied}`。

可選 **Cloudflare Worker 代理**（deploy/worker.js）：白名單 host、注入 secret key、加 CORS 標頭、`?u=` 帶目標 URL。

---

## 第 6 部分：UI / 路由 / Memo（app.js）

狀態：`ANALYSES[]、PORT、VIEW("desk")、CUR`。`compute()` = 全場 analyzeMatch + analyzePortfolio。
`twTime(iso)` 用 `Intl.DateTimeFormat("zh-TW",{timeZone:"Asia/Taipei",...})`。
色彩：conf≥75 綠 / ≥60 琥珀 / else 紅。

四個分頁（底部 tab 切換）+ 單場頁：
1. **主控台 renderDesk**：四場組合分卡 + 情報警報（傷病/高溫/盤口反向/X 級）+ 高 Edge 標的（maxEdge≥0.03）+ 今日賽程列（每列顯示 λ、台灣時間、T-Nh、評級徽章）。
2. **單場頁 renderMatch(id)**：表頭 → 投資摘要(主判斷/模型/市場/Edge/信心/是否納入) → 1X2 機率條 + **集成估計** → Edge 表(5市場) → 模型 KPI(λ/總進球/大2.5/BTTS/角球) → 比分熱力圖 scoreHeat(0~5) + 核心/黑馬比分 → Monte Carlo(主勝+90%CI+CV) → 戰術對位(A4 分數+風格+量化因子) → 歷史交手+底蘊 → 市場盤口(初盤/即時/移動/抽水/資金訊號) → 風險清單 → 最終結論 + 「產生 Memo」按鈕。
3. **四場組合 renderPortfolio**：PORTFOLIO SCORE + 各項懲罰 + 相關性風險地圖 + 四場信心排序 + 組合輸出表。
4. **時間軸 renderTimeline**：T-72h→賽後 六階段 checklist + **回測區**（有 SID.results 即顯示 Brier/LogLoss/命中/可靠度，否則顯示待填提示）。
5. **設定 renderSettings**：資料來源徽章 + Live 對接表單(provider/key/proxy/season) + 「測試連線並更新」。

**Memo（buildMemo(a)）**：十段純文字 — ①投資摘要(含集成) ②球隊基本面(FIFA/Elo/近況/Elo差/期望淨勝) ③球員資產(缺陣/歷史/底蘊) ④戰術對位(A4) ⑤環境水土 ⑥模型輸出(1X2/MC/大小分/BTTS/λ/角球) ⑦比分矩陣(核心/黑馬) ⑧市場盤口 ⑨風險清單 ⑩最終結論(主判斷/備選/信心/風險等級)。

**導出供測試**：`SID._test = { compute, renderDesk, renderMatch, renderPortfolio, renderTimeline, renderSettings, buildMemo, matchRow, scoreHeat, getAnalyses, twTime, ... }`。

`index.html` 依序載入 6 支 js，並在 `http(s)` 下註冊 `sw.js`（`file://` 自動略過）。

---

## 第 7 部分：建置 / 測試 / 部署

### 7.1 build.js（契約見下，零依賴）
產出 `dist/SID-standalone.html`（CSS+6 JS 內嵌、移除 manifest/sw、健檢無殘留外部引用否則 exit 1）
與 `dist/site/`（部署根目錄，只含 index/manifest/sw/css/js，**排除測試碼**）。

### 7.2 qa.js（驗收門檻，**必過**）
DOM 最小 shim 後載入全部模組，跑 10 輪（預設）共驗 10 面向（見「驗收標準」），
輸出 `PASS N / FAIL 0`。任一 FAIL 不得上線。支援 `node qa.js 30` 壓測。

### 7.3 backtest_sim.js
固定種子合成 DGP（與引擎異構、含觀測雜訊、對手為含抽水市場），跑數千場，
報告：集成/模型/市場/真相/均勻 的平均 Brier、Log Loss、Brier Skill Score、
可靠度分箱、Edge 資訊量、分級平均 Brier。**誠實聲明合成性質**。

### 7.4 部署（Cloudflare Pages，三法擇一）
- **A 拖拉**：Dashboard → Pages → Upload assets → 拖 `dist/site/` → 取新專案名 → Deploy。
- **B wrangler**：`CLOUDFLARE_API_TOKEN=<Pages:Edit> npx wrangler pages deploy dist/site --project-name <name>`（環境需放行 `api.cloudflare.com`）。
- **C 連 Git**：Root `sports-intelligence-desk`、Build `node build.js`、Output `dist/site`。
> 每個 Pages 專案天生隔離（獨立 `*.pages.dev`）；取新名即與既有專案無關。

---

## 第 8 部分：給執行 AI 的分階段建置計畫

> 建議順序，每階段做完都應能 `node qa.js` 局部驗證（先放寬對應斷言）。

1. **Phase 0 — 骨架**：建 `window.SID`、`index.html`、`css/terminal.css`（深色終端風）、模組載入順序。
2. **Phase 1 — 資料層**：依 §1 schema 填 teams/players/matches/odds/meta（用查證快照，缺則標無）。
3. **Phase 2 — 引擎數學**：依 §3.0–3.7 實作 poisson/Dixon-Coles/期望進球/校準/deriveMarkets；先讓 1X2、大小分、比分矩陣正規化通過。
4. **Phase 3 — 評級與市場**：§3.10–3.12 marketImplied/gradeMatch/classifyScores。
5. **Phase 4 — 不確定度與集成**：§3.13 蒙地卡羅、§3.14 analyzeMatch 的 cv 與 ens。
6. **Phase 5 — 組合與歷史**：§3.15 portfolio、§2 history。
7. **Phase 6 — 回測**：§4 backtest.js + backtest_sim.js（合成 DGP 校準 γ 與 ENS_W）。
8. **Phase 7 — UI/Memo**：§6 app.js 五頁 + Memo + `_test` 導出。
9. **Phase 8 — Live**：§5 live.js（三 provider + 傷病同步 + 降級）+ 可選 worker。
10. **Phase 9 — 建置/測試/部署**：§7 build.js、qa.js（10 面向斷言）、Cloudflare Pages。

---

## 第 9 部分：驗收標準（Definition of Done）

**機械正確性（qa.js 必全綠）**
- [ ] 模組與導出齊全（SID 及 analyzeMatch/analyzePortfolio/monteCarlo/runBacktest/refresh/history）
- [ ] 1X2、比分矩陣、Poisson、集成 均正規化（Σ=1，<1e-6）；大小分單調(O1.5≥O2.5≥O3.5)
- [ ] 蒙地卡羅：avgGoals≈λH+λA、區間含點估計、1X2 和≈1
- [ ] 回測自檢 0/2/0.667；命中判定正確
- [ ] 資料健全：Elo 範圍、xG>0、賠率>1、抽水 0–25%、**無捏造傷病**
- [ ] 時區 UTC→Asia/Taipei 正確
- [ ] 五頁 + 單場頁 + Memo 渲染 **無 undefined/NaN/[object**
- [ ] 邊界：極端 Elo 錯配、缺球員、極小 λ 不崩
- [ ] Live 未設定→降級；傷病同步只動 curated、不造假
- [ ] 評級 ∈ {S,A,B,C,X}、組合分 0–100、每場有 hist/mc/ens/tacScore
- [ ] 多輪壓測（`node qa.js 30`）FAIL=0（無狀態污染）

**部署**
- [ ] `node build.js` 產出 `dist/site/`（含 index.html）
- [ ] Cloudflare Pages 獨立專案上線、`https://<name>.pages.dev` 可開、四頁正常

**誠實揭露（交付對象須知，非 bug）**
- [ ] Edge 非獲利保證（合成回測資訊量可能 <50%）；最可信為 A5 集成
- [ ] 評級衡量「價差」非「準度」；真實 Alpha 須賽後回填 SID.results 累積

---

## 附錄 A：可複現的範例資料（4 場 2026 WC H/G 組）
依 §1 schema，建 8 隊（ESP/KSA/BEL/IRN/URU/CPV/NZL/EGY）與 4 場（ESP-KSA、BEL-IRN、URU-CPV、NZL-EGY），
Elo/xG/盤口用查證快照。球員只列已查證核心（如 ESP: Yamal/Pedri/Simón；EGY: Salah/Marmoush），
**未查證傷病一律不填**。此即 qa.js 與 backtest 的測試底座。

## 附錄 B：關鍵常數速查
```
TOURNAMENT_AVG_XG=1.30  CALIB_GAMMA=1.25  ENS_W=0.4
Dixon-Coles rho=−0.06   MAXG=8   Elo→goals=0.28/100  sup∈[−2.6,2.6]
λ∈[0.18,4.2]  attackMult∈[0.55,1.05] concedeMult∈[0.95,1.6] envMult∈[0.8,1.05]
MC cv 基礎 0.16（+傷病0.04/+環境0.03/+小樣本0.03）  batch=500  CI=5%/95%
grade: S(edge≥.06,risk≤12,conv≥.5) A(edge≥.035,risk≤20) B(edge≥.02) X(risk≥25或edge<0,或risk≥30) else C
conf=clamp(50+edge·220+(conv−.4)·40−risk, 5, 97)
baselineBrier=0.667
```
