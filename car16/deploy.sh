#!/usr/bin/env bash
# car16 一鍵部署到 Cloudflare Pages — 含 token / egress 預檢（沿用 SID 模式）
# 用法：PROJECT=car16 ./deploy.sh   （或 make deploy）
set -euo pipefail

PROJECT="${PROJECT:-car16}"
API_HOST="api.cloudflare.com"

cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
fail() { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  fail "缺少 CLOUDFLARE_API_TOKEN。請把它加進環境變數（token 權限需含 Account · Cloudflare Pages · Edit + Workers Scripts · Edit + D1 · Edit）。"
fi

say "→ 預檢 egress：$API_HOST"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
  "https://$API_HOST/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" 2>&1 || true)"
case "$code" in
  200) say "  ✓ token 有效、網路通" ;;
  000|"") fail "連不到 $API_HOST（egress 被擋）。環境設定 → Network access → 加入 $API_HOST 與 *.cloudflare.com 後開新 session。" ;;
  401) fail "token 無效（HTTP 401）。請重簽含 Pages:Edit 的 token。" ;;
  403) fail "HTTP 403 — egress 防火牆或 token 權限不足，請檢查兩者。" ;;
  *)   say "  ⚠ 預檢回 HTTP $code，仍嘗試部署…" ;;
esac

say "→ build dist/site/"
node build.js >/dev/null

say "→ wrangler pages deploy（專案：$PROJECT）"
npx --yes wrangler@latest pages deploy dist/site \
  --project-name "$PROJECT" \
  --commit-dirty=true

say ""
say "✅ 前端部署完成 → https://$PROJECT.pages.dev"
say "   API Worker 另跑：make deploy-worker"
