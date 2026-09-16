#!/usr/bin/env bash
# POS 部署預檢 — 在動手部署前確認環境開通狀態（照 deploy-sid 的預檢風格）。
# 用法: bash scripts/deploy_preflight.sh
# 全部綠燈後，跟 Claude 說「部署 POS」即可開始。詳見 docs/24_pos_deploy_runbook.md。

set -u
PASS=0; FAIL=0

ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; echo "     → $2"; FAIL=$((FAIL+1)); }

echo "── POS 上線預檢 ──"

# 1) Zeabur token（跑主機用）
if [ -n "${ZEABUR_API_KEY:-}" ]; then
  ok "ZEABUR_API_KEY 已設定"
else
  bad "缺少 ZEABUR_API_KEY" "Zeabur 後台 (zeabur.com) → 頭像 → Settings → API Key 產生一把，貼進 Claude Code 環境設定的 Environment variables，開新 session。"
fi

# 2) Cloudflare token（綁自訂網域用，選填）
if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
  ok "CLOUDFLARE_API_TOKEN 已設定（可綁自訂網域）"
else
  echo "  ⚪ CLOUDFLARE_API_TOKEN 未設定 — 沒有它也能部署，會先用 *.zeabur.app 網址；要綁自己的網域才需要。"
fi

# 3) 網路 egress
probe() {  # host -> 0 reachable
  curl -s -o /dev/null --max-time 8 "https://$1/" 2>/dev/null
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$1/" 2>/dev/null)" != "000" ]
}
if probe api.zeabur.com; then
  ok "api.zeabur.com 連得到"
else
  bad "api.zeabur.com 被 egress 擋住" "claude.ai/code → 雲朵圖示 → 環境齒輪 → Network access → Custom → Allowed domains 加: zeabur.com *.zeabur.com api.zeabur.com zeabur.app *.zeabur.app — 存檔後開新 session。"
fi
if probe api.cloudflare.com; then
  ok "api.cloudflare.com 連得到"
else
  echo "  ⚪ api.cloudflare.com 被擋 — 綁自訂網域時才需要；白名單加 api.cloudflare.com *.cloudflare.com。"
fi

echo "──"
if [ "$FAIL" -eq 0 ]; then
  echo "✅ 預檢通過 — 跟 Claude 說「部署 POS」即可。"
else
  echo "❌ 還有 $FAIL 項未開通（見上方 → 指引）。改完環境設定後務必開新 session 再跑一次本腳本。"
  exit 1
fi
