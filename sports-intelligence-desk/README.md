# Sports Intelligence Desk｜賽事情報投研終端

> **不是預測器，是投研終端。**
> 每場比賽 = 一檔即將開盤的資產。系統只回答五件事：
> 真實機率多少？市場怎麼定價？哪裡有錯價(Alpha)？風險可控嗎？這場該不該納入四場組合？

獨立專案，與本 repo 其他系統零依賴。純前端、可離線、手機可裝成 App（PWA）。

---

## 立刻使用

**手機 / 電腦直接開：**
```
sports-intelligence-desk/index.html
```
雙擊用瀏覽器打開即可，無需後端、無需安裝。

**裝成手機 App（PWA）** — 需用本機伺服器開（service worker 需 http）：
```bash
cd sports-intelligence-desk && python3 -m http.server 8080
# 手機瀏覽器開 http://<電腦IP>:8080 → 加到主畫面
```

---

## 目前標的（2026 FIFA World Cup，台灣 6/22）

| 場次 | 組 | 場館 |
|---|---|---|
| 🇪🇸 西班牙 vs 沙烏地阿拉伯 🇸🇦 | H | Atlanta |
| 🇧🇪 比利時 vs 伊朗 🇮🇷 | G | SoFi, LA |
| 🇺🇾 烏拉圭 vs 維德角 🇨🇻 | H | Miami（高溫高濕）|
| 🇳🇿 紐西蘭 vs 埃及 🇪🇬 | G | Vancouver |

要換成任何其他比賽：改 `js/data.js` 的 `SID.matches` / `SID.teams` 即可，引擎與畫面不用動。

---

## 三個畫面

- **主控台** — 組合總分、情報警報（傷病/高溫/盤口異動/X級不碰）、高 Edge 標的、今日賽程（台灣時間 + 倒數）
- **單場投研頁** — 投資摘要 → 勝平負 → Edge 表 → 模型 KPI → 比分矩陣熱力圖 → 戰術對位 → 盤口分析 → 風險清單 → 最終結論 →「產生完整投研 Memo」
- **四場組合頁** — Portfolio Score、相關性風險地圖、信心排序、組合輸出表
- **時間軸** — T-72h→T-90m→賽後 投研節奏 + 回測欄位

---

## 模型引擎（`js/engine.js`，8 子模型）

| 模型 | 作用 |
|---|---|
| M1 Team Power | Elo + 近況 + 經驗 + 教練 → 戰力差 |
| M2 Expected Goals | 攻防乘子 Poisson **融合** Elo supremacy → λA, λB |
| M3 Score Matrix | Poisson + **Dixon-Coles 低比分修正** → 9×9 矩陣，導出 1X2 / 大小分 / BTTS / 正確比分 |
| M4 Corners | 打法 + 對手低位 + 壓制 → 總角球期望 |
| M5 Tactical | 風格對位 → 突破口文字化 |
| M6 Availability | 核心球員缺陣/存疑 → 調整 λ（進攻↓ / 失球↑）|
| M7 Environment | 濕球溫度 / 時差 / 移動 / 休息 → 體能折損 + 信心扣分 |
| M8 Market | 去抽水隱含機率 vs 模型機率 → **Edge (Alpha)** + 評級 S/A/B/C/X |

**評級**：S 三方一致核心場｜A 優勢明確｜B 有方向變數多｜C 觀望｜X 不碰
**組合分**：平均信心 − 相關性 − 過熱 − 資訊不完整懲罰

---

## P0 對照（已全部實作）

✅ 自動賽程　✅ 台灣時間轉換　✅ 球隊基本面表　✅ 近10場戰績
✅ 勝平負模型　✅ 大小分模型　✅ 正確比分矩陣　✅ 四場組合評分
（附加：角球模型、球員傷病、盤口異動、賽後回測欄位 = P1）

---

## 接 Live 即時資料

`js/data.js::SID.fetchFixtures()` 已預留接口。接 API-Football / Sportmonks
後映射成同樣結構，引擎與畫面**完全不用改**。目前用內建快照（2026-06-19），
無需 API key、可離線運作。

---

## 驗證

```bash
node -e 'global.window={};require("./js/data.js");require("./js/engine.js");
const S=window.SID;const A=S.matches.map(m=>S.analyzeMatch(m));
A.forEach(a=>console.log(a.home.name,a.grade.grade,a.grade.conf));
console.log(S.analyzePortfolio(A).score)'
```
