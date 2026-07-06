# TIGER LINE PRIME — 部署指南 (Sprint 7)

三種用法，選一種：

| 用法 | 適合 | 起動時間 | 需要什麼 |
|---|---|---|---|
| A. 本機直接跑 | 你在自己的電腦決策 | 30 秒 | Python 3.12 + venv |
| B. Docker 本機容器 | 想跟朋友共用一台機 | 2 分鐘 | Docker |
| C. Fly.io 雲端 | 手機隨開隨用（推薦） | 5 分鐘 | Fly.io 免費帳號 |

---

## A. 本機直接跑

```bash
# 專案根目錄
.venv/bin/pip install fastapi 'uvicorn[standard]'   # 若還沒裝
.venv/bin/uvicorn tigerline.web:app --host 127.0.0.1 --port 8000
```

打開瀏覽器：http://127.0.0.1:8000

---

## B. Docker 本機

```bash
# 專案根目錄（Dockerfile 用的是相對於根目錄的 COPY tigerline）
docker build -f tigerline/Dockerfile -t tigerline:latest .
docker run --rm -p 8000:8000 tigerline:latest
```

打開瀏覽器：http://localhost:8000

映像檔大約 200 MB — 只含 fastapi/uvicorn/pydantic/pyyaml/typer + `tigerline`
套件，沒有 `restaurant_api` / `devswarm` 的重型依賴。

---

## C. Fly.io 雲端（推薦）

Fly.io 有永久免費額度（一台 shared-cpu-1x 256 MB VM，24 小時閒置自動關機，
零流量時完全不收費）。這台服務就是你的個人賽事決策 API + 前端，手機打開網址
就能用，不用開電腦。

### 一次性設定

```bash
# 1. 安裝 flyctl
curl -L https://fly.io/install.sh | sh

# 2. 登入（會開瀏覽器）
fly auth login

# 3. 建立 app（在 tigerline/ 目錄下跑）
cd tigerline
fly apps create tigerline-<你的名字>   # e.g. tigerline-ivan
```

### 每次部署

```bash
# 從專案根目錄
fly deploy --config tigerline/fly.toml \
           --dockerfile tigerline/Dockerfile \
           --app tigerline-<你的名字>
```

第一次會花 3-5 分鐘 build + upload。之後改 code 再 deploy 大約 90 秒。

### 打開它

```bash
fly open --app tigerline-<你的名字>
# 或直接看網址
fly status --app tigerline-<你的名字>
```

網址長這樣：`https://tigerline-ivan.fly.dev`

**存進手機書籤**，之後點開就是決策工具。

### 費用預估

- Fly.io 免費額度：3 台 shared-cpu-1x/256MB VM，一個月 160 GB 出站流量
- 這台服務：1 台 VM，24 小時無流量會自動 `stop`（`auto_stop_machines = "stop"`）
- 有請求進來時 Fly 會在 200 ms 內自動 `start`
- 個人用途一個月大概 $0 - $2 USD

---

## 驗證部署成功

任何用法起來以後，跑這幾個檢查：

```bash
# 健康檢查
curl https://<你的網址>/health
# 預期：{"status":"ok","service":"tigerline","version":"3.0.0"}

# 列範例
curl https://<你的網址>/api/examples
# 預期：{"examples":["belgium_nz_two_goal","egypt_iran_pressure_under","rotation_trap_skip"]}

# 一次完整 analyze
curl -X POST https://<你的網址>/api/analyze \
  -H "content-type: application/json" \
  -d @tigerline/examples/belgium_nz_two_goal.json
# 預期：scenario="two_goal_landing"、main_bet.selection="Belgium -1.5"
```

---

## 目前的限制（Sprint 7 明文擋住 scope creep）

- **無資料庫** — analyze / review 都無狀態，每次請求自帶完整輸入
- **無登入** — 這是個人工具，網址知道就能用
- **無 CLV / 走勢** — 那些 V3.0 功能需要 SQLite + 存 snapshot；下一個 Sprint 補
- **無多平台** — 一份 app，一個 URL

---

## Rollback

Fly.io 每次 deploy 都是新的 image release，可以 rollback：

```bash
fly releases --app tigerline-<你的名字>
fly releases rollback <version> --app tigerline-<你的名字>
```

---

## 修 config

想改 region / VM 規格 / 域名：編輯 `tigerline/fly.toml`，然後 `fly deploy`。
