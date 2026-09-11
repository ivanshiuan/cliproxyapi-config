# 20 — WrenAI（語意化 SQL / GenBI）設定與使用

> 什麼時候用 WrenAI：想用**自然語言問資料**（「上週哪家店營收最高」「本月客單價趨勢」），
> 讓它透過語意層（MDL）自動轉成 SQL 打進資料庫；或想把一個資料庫 schema
> 產成可治理的語意專案、加業務語境（單位、enum 意義、ARR/DAU 這種 cube）、
> 甚至一鍵產出可分享的 GenBI 儀表板網站。它是 text-to-SQL 的「有治理版」。

來源：<https://github.com/Canner/WrenAI> — Canner 開源的 GenBI 引擎。

---

## 這是什麼 / 裝了什麼

WrenAI 新版（0.13+）已經**不是 Docker Compose 一大包**，改成一支
**`wren` CLI + agent 技能**的形態（舊 Docker 版留在 `legacy/v1` 分支）。
本專案是 Claude Code repo，所以走官方推薦的 agent 路線：

| 元件 | 位置 | 進版控？ | 說明 |
|---|---|---|---|
| 技能探索 stub | `.claude/skills/wren/SKILL.md` | ✅ 是 | Claude Code 每個 session 自動載入；觸發字見檔頭 |
| 技能正本 | `.agents/skills/wren/`、`skills-lock.json` | ✅ 是 | `npx skills` 的跨 agent 正本 + lockfile |
| `wren` CLI 執行檔 | `~/.local/bin/wren`（uv tool） | ❌ 否 | **ephemeral**，容器回收就沒了，靠下面的腳本重裝 |

**關鍵**：技能檔已進版控，fresh clone 會自動載入；**只有 CLI 執行檔是暫時的**。

---

## 新 session 重裝 CLI（一行）

容器是 ephemeral，`wren` 執行檔不進版控。新的 cloud session 補裝：

```bash
bash scripts/setup_wren.sh
```

腳本用 `uv tool install "wrenai[postgres,memory,mcp]"`，可重複執行、失敗不擋
session。要**每次 session 自動裝**，就把這支貼進 claude.ai/code →
環境齒輪 → **Setup script**（跟 `setup_browser_act.sh` 同一招）。

extras 說明：`postgres`=接本專案 PG、`memory`=LanceDB 語意記憶（`wren ask` 用）、
`mcp`=`wren serve mcp` 當 MCP server。要接別的資料源再加，例如
`wrenai[postgres,bigquery,snowflake,memory,mcp]`。

### 網路白名單（Network access = Custom 時）

| 用途 | 要放行 |
|---|---|
| 裝 `wren`（pip/uv 下載） | `pypi.org` `files.pythonhosted.org`（uv 未預裝再加 `astral.sh`） |
| `npx skills` 重新同步技能（選用，已進版控可略） | `github.com` `*.githubusercontent.com` |
| `wren ask` / genbi 讓 Claude 產 SQL | `api.anthropic.com`（或你用的 LLM 供應商） |

---

## 怎麼用（工作流程指南活在 CLI 裡）

WrenAI 的實際操作指南**不寫死在技能檔**，而是內建在 CLI，永遠跟版本同步：

```bash
wren skills list                 # 列出所有工作流程指南
wren skills get onboarding       # 端到端：把資料庫接上、產 MDL
wren skills get usage            # 日常查詢
wren skills get generate-mdl     # 從 DB schema 產語意層 MDL
wren skills get enrich-context   # 加業務語境（單位、enum、cube）
wren skills get genbi            # 產出可分享的 GenBI 網站並部署
```

驅動任何多步流程前，先 `wren skills get <name>` 載入對應指南（技能檔頭已註明鐵律）。

日常指令（都是 top-level，不是 sub-app）：

```bash
wren --sql 'SELECT 1'                    # 透過 MDL 語意層跑 SQL
wren dry-plan --sql '...'                # 只轉譯、不打 DB
wren context show / build / validate     # MDL 專案生命週期
wren profile add / list / switch         # 具名連線設定檔（~/.wren/profiles.yml）
wren memory index / recall               # 語意記憶（需 [memory] extra）
wren ask "上個月營收前三高的店" --guided   # 把問題包成給 agent 的 prompt
wren serve mcp                           # 把 Wren 當 MCP server 給 agent 用
```

---

## 手把手 demo(零依賴,現在就能跑)

`wren/demo/` 是一個自足的範例專案(餐飲形狀假資料 → 本地 DuckDB),用來證明整條路
是通的、也當作接真資料前的沙盒:

```bash
make wren   # 一鍵冪等:裝 CLI + 重建資料 + 編譯 MDL + 跑驗證查詢(可重複跑)
```

會印出三家店營收排行(逢甲 3090.00 / 信義 2730.00 / 西門 430.00,金額為 DECIMAL)。
細節與如何改成真實資料庫見 `wren/demo/README.md`。

## 把本專案的 restaurant Postgres 接上

`restaurant_api` 的開發 PG 預設（見 `restaurant_api/config.py`，SessionStart hook 會起它）：

| 欄位 | 值 |
|---|---|
| host | `localhost` |
| port | `5432` |
| database | `resto_dev` |
| user | `resto` |
| password | `resto_dev_password`（dev 預設，正式環境用 `.env` 覆蓋） |

連線欄位以 `wren docs connection-info postgres` 為準(host / port / database / user / password)。
接上去的正路(agent 驅動,一步一步):

```bash
wren profile add resto        # 建 resto profile(帳密走 .env,不在對話裡輸入)
wren profile switch resto     # 切成 active
wren skills get generate-mdl  # 讓 /wren 技能探索 schema、自動產 models/
wren context build            # 編譯 MDL
WREN_PROJECT_HOME=$PWD wren --sql 'SELECT ...' \
  --connection-info '{"datasource":"postgres","host":"localhost","port":"5432","database":"resto_dev","user":"resto"}'
```

> 註:`wren --sql` 要在有 `wren_project.yml` 的專案內跑(或設 `WREN_PROJECT_HOME`);
> `--connection-info` 的 JSON 一定要含 `"datasource":"postgres"` 這個 key(缺了會報錯)。

> ⚠️ 帝國鐵律沿用：正式資料庫連線資訊走 `.env`、**永不進 git**；
> DevSwarm/工具**永遠不要指到 production credentials**。上面是 dev 預設值才敢寫死。

---

## 要真正跑起來還缺什麼

1. **LLM API key** — `wren ask` / `genbi` 要 LLM 當 text-to-SQL 引擎。
   本 repo 用 Claude：`.env` 填 `ANTHROPIC_API_KEY`（session 啟動時若缺會告警）。
2. **一個資料庫連線** — 上面的 restaurant PG，或 `wren docs connection-info <ds>`
   查別的資料源要哪些欄位。

## 選用：少一點權限提示

想跑 `wren` 不每次被問，可在 `.claude/settings.json` 的 `permissions.allow`
加 `"Bash(wren:*)"`（技能檔 frontmatter 已把 `allowed-tools` 限定在 `Bash(wren:*)`）。
沒加也能用，只是會多一次確認。
