# 選牌通 PlateGo — 台灣車牌選號整合平台

> 全台監理所站可選號牌一站查詢 + 標牌競標情報 + 代標服務。
> 對標 car16.com 的業務型態，但**全站 RWD（mobile-first）**、設計與程式皆為原創，
> 並把「代標競標」做成主力營收業務。

## 目錄結構

```
plate_service/
├── README.md              # ← 你在這
├── docs/
│   └── business_plan.md   # 業務規劃：市場、產品、營收、合規
├── web/                   # 純靜態前端（可直接部署 Cloudflare Pages）
│   ├── index.html         # 首頁：全國選號查詢、服務、方案、FAQ
│   ├── auction.html       # 標牌競標公告 + 成交行情
│   ├── css/style.css      # mobile-first RWD（640 / 960 / 1200 斷點）
│   ├── js/                # common / app / auction，無框架、無外部依賴
│   └── data/*.json        # 資料層（demo 由腳本產生；正式由 scraper 同步）
└── scraper/
    ├── generate_demo_data.py  # 產生示範資料（種子固定、可重現）
    └── mvdis_client.py        # 監理服務網公開資料 client（骨架 + demo 模式）
```

## 本機預覽

```bash
python3 -m http.server 8080 -d plate_service/web
# 開 http://localhost:8080
```

前端用 `fetch()` 載入 JSON，**必須**經 HTTP 伺服器開啟（直接雙擊 html 會被
瀏覽器 CORS 擋掉）。

## 重新產生 demo 資料

```bash
python3 plate_service/scraper/generate_demo_data.py
```

## 資料流設計

```
監理服務網（公開頁面）          scraper/mvdis_client.py         web/data/*.json
┌──────────────────────┐   低頻排程（每日）+ 禮貌抓取   ┌──────────────┐
│ 可選號牌查詢（有驗證碼）│ ────────────────────────────▶ │ plates.json   │
│ 標牌公告 / 競標中      │                               │ auctions.json │──▶ 前端
│ 標售紀錄（歷史成交）    │                               │ records.json  │
└──────────────────────┘                               └──────────────┘
```

- demo 與正式資料**格式完全一致**，切換資料源前端零改動。
- 競標中/標售紀錄頁無驗證碼，最適合先自動化；可選號牌查詢有圖形驗證碼，
  以低頻 + 人工輔助處理，**不做驗證碼自動破解**（合規紅線，見 scraper docstring）。

## 部署

純靜態站，任何靜態託管皆可。建議沿用本 repo 既有的 Cloudflare Pages 流程：

```bash
npx wrangler pages deploy plate_service/web --project-name platego
```

## 免責與合規要點

- 非官方網站；選號、出價、繳費一律導回監理服務網完成。
- 車牌**不得單獨買賣轉讓** — 代標一律以委託人名義得標，本站僅提供代辦與顧問。
- 示範站台所有號牌、出價、成交數字皆為模擬資料。
