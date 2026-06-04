# 11 — Production Deployment

> 從 `make api` 跑得起來，到「真的丟雲端讓員工點餐」的最短路徑。
> Phase 1 目標：單一 VM 跑得穩，3 個月內驗證商模再上 K8s。

---

## 0. 部署拓撲（單店 MVP）

```
                    Cloudflare (TLS、WAF、DDoS、CDN)
                              │
                              ▼
                ┌──────────── VM (4 vCPU / 8 GB) ────────────┐
                │                                            │
                │   resto-api   ──▶  Postgres 16 + pgvector  │
                │     │                  ▲                   │
                │     │                  │                   │
                │   resto-jobs (scheduler)                   │
                │     │                  │                   │
                │     └──▶ Redis 7  (快取 / 隊列備援)         │
                └────────────────────────────────────────────┘
                              │
                              ▼
                  LINE / ECPay / iCHEF / Google Maps API
```

理由：單店每日 < 5,000 訂單，3 個容器在 4 vCPU VM 跑綽綽有餘；連鎖到第 5 家再上 K8s。

---

## 1. 三大門檻（缺一不可）

| 項目 | 取得管道 | 預估天數 |
|---|---|---|
| **網域 + Cloudflare** | 任一網域商 → Cloudflare 接 DNS | 1 天 |
| **VM**（Linode / Vultr / Hetzner / GCP e2-small / AWS t4g.medium） | 月費 $20-40 USD | 1 小時 |
| **電子發票字軌** | 財政部電子發票整合服務平台 | **30-60 天** ← 最容易卡 |

額外建議：**LINE 官方帳號驗證**（10-30 天）、**支付閘道**（街口 / LINE Pay / ECPay，7-14 天）。

---

## 2. 環境檔分離（重要）

| 檔名 | 用途 | 是否 commit |
|---|---|---|
| `.env.example` | 範本，不含真實值 | ✅ commit |
| `.env` | 本地開發 | ❌ gitignored |
| `.env.production` | 真實上線值（含 LINE token、DB password、發票 API key） | ❌ **絕對不 commit**；用密碼管理器存 |

`.env.production` 應該存在：
- 1Password / Bitwarden / Vault — 主備份
- VM 上 `~/.env.production`（root 不可讀，僅 deploy user）
- 不要存在你的筆電 Downloads/

---

## 3. 部署 SOP（每次發版）

```bash
# 1) 本機跑全部閘門
make full-check
# → 106+ tests passed, ruff clean, pyright 0, no migration drift

# 2) Tag 版本（semver）
git tag -a v0.1.0 -m "Phase 1 MVP — single store"
git push origin v0.1.0

# 3) 在 VM 上拉新版本
ssh deploy@your-server
cd /opt/resto
git fetch && git checkout v0.1.0

# 4) Build image（首次 / 有依賴變更時）
docker build -t resto-api:v0.1.0 -f restaurant_api/Dockerfile .

# 5) 滾動發佈 — migrate 先跑，舊 api 還在服務
RESTO_IMAGE=resto-api:v0.1.0 docker compose \
    -f restaurant_api/docker-compose.production.yml \
    up -d --no-deps migrate
# 等 migrate 完成（exit 0）

# 6) 起新 api，舊 api 自然被換掉
RESTO_IMAGE=resto-api:v0.1.0 docker compose \
    -f restaurant_api/docker-compose.production.yml \
    up -d --no-deps --force-recreate api jobs

# 7) 確認新版正常
curl -fsS https://api.your-domain.tw/health/ready | jq
curl -fsS https://api.your-domain.tw/version
```

---

## 4. 監控紅線

| 紅燈 | 量測來源 | 應對 |
|---|---|---|
| `/health/ready` 連續 3 次 503 | Cloudflare uptime monitor | LB drain、查 DB |
| `cogs.variance.alert` audit_log 出現 | nightly job → audit_log → 店長 LINE | 隔日盤點 |
| `expiry.warning` 連 3 天未處理 | audit_log | 店長帳號被警告 |
| Pod RSS > 400 MB | docker stats / Prometheus | 重啟、看 query |
| `http.request.error` 結構化日誌 > 5/min | log aggregator | 查 request_id 鏈 |
| 一日無 `order.created` audit | log | POS 斷線 / 員工操作異常 |

---

## 5. 備份策略（必要！）

```bash
# 每日 03:50（在 expiry / points / variance 三 job 都完成後）
docker exec resto-db pg_dump \
    -U resto -d resto_prod -Fc -Z 9 \
    -f /backups/resto-$(date +%Y%m%d).dump

# 上傳到異地（S3-compatible / Backblaze B2 / Wasabi）
rclone copy /backups/ b2:resto-prod-backups/

# 每月 1 號自動 restore 測試（驗證 dump 真的能 restore）
pg_restore --create -d postgres -h test-host /backups/resto-$(date +%Y%m01).dump
```

**沒測過 restore 的 backup 等於沒 backup**。

---

## 6. 災難復原（DR）

| 情境 | RPO | RTO | SOP |
|---|---|---|---|
| VM 整台掛 | 24h（每日 backup） | 2h | 啟新 VM → restore dump → DNS 切換 |
| Postgres 資料毀損 | < 1h（WAL 啟用後） | 1h | restore + WAL replay |
| 程式版本爆掉 | 0 | 5 min | `docker compose up -d --no-deps api` 切回上一 image tag |
| 機房斷網 | 0 | 視機房 | Cloudflare cache 撐住 GET，POST 排隊紙本接單 |

---

## 7. 不要做（學費）

- ❌ 用 `latest` tag — 沒有回滾路徑
- ❌ 把 `.env.production` 放在 docker image 裡 — 一被 pull 就全洩
- ❌ 跳過 `migrate` service 直接上 `api` — 新欄位讀不到會 500
- ❌ 在主資料庫上跑分析 query — 用 read replica（Phase 2 補）
- ❌ 用 root 跑 container — Dockerfile 已切到 uid 10001
- ❌ 把 8000 直接對外 — 必須走 Cloudflare TLS termination
- ❌ 接 LINE webhook 不驗證 signature — `restaurant_api/integrations/line/messenger.py` 的 channel_secret 就是為這個

---

## 8. 開店日 T-7 檢查清單

- [ ] `.env.production` 已存在 1Password
- [ ] DNS 已指向 Cloudflare → VM
- [ ] TLS 證書已部署（Cloudflare Universal SSL）
- [ ] `/health/ready` 從外網跑得通
- [ ] `make demo-flow` 在 production DB 跑過一次（測試 tenant）
- [ ] 每日 backup 已跑 7 天且至少測過 1 次 restore
- [ ] 電子發票字軌已申請、API key 已填
- [ ] LINE 官方帳號已驗證、channel_secret + access_token 已填
- [ ] 店長 / 收銀員 已有自己的 account（Phase 2 補 auth 後）
- [ ] 紙本訂單本 2 本已備在收銀台（POS 當機 SOP）
- [ ] docs/08_safety_compliance.md 已印一份貼在廚房

---

## 9. Phase 2 部署升級路徑

當你有第二家店、或日訂單 > 5,000：

1. **DB 拆出去**：用 GCP Cloud SQL / RDS 託管 PG
2. **多 replica**：api container 跑 2-4 個、Cloudflare LB
3. **Jobs 改 Celery**：scheduler 不能多 replica，必須改成 dedicated worker
4. **K8s**：上面那套 compose 翻成 Helm chart
5. **多 region**：Cloudflare R2 / S3 for static、PG 改 read replica

切換時機：當你開始說「我手動 ssh 上去操作 production」覺得心累。
