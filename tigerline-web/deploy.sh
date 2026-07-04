#!/usr/bin/env bash
# TIGER LINE PRIME — one-command Cloudflare Pages deploy.
# Mirrors sports-intelligence-desk/deploy.sh so both projects use the
# same operational muscle-memory.
#
# Usage (either):
#   PROJECT=tigerline ./deploy.sh
#   ./deploy.sh                # uses default PROJECT below
#
# Pre-req (one-time, in the env this runs from):
#   1. CLOUDFLARE_API_TOKEN with "Cloudflare Pages: Edit" scope
#   2. api.cloudflare.com + *.cloudflare.com allowed in egress
set -euo pipefail

# Default project name — matches "TigerLine" branding. Change with PROJECT=...
PROJECT="${PROJECT:-tigerline}"
API_HOST="api.cloudflare.com"

cd "$(dirname "$0")"

say()  { printf '%s\n' "$*"; }
fail() { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

# 1) Token present?
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  fail "缺少 CLOUDFLARE_API_TOKEN。
    - 網頁環境：Settings → Environment variables → 加 CLOUDFLARE_API_TOKEN，再開新 session。
    - 本機：export CLOUDFLARE_API_TOKEN=...   （token 從 https://dash.cloudflare.com/profile/api-tokens 開，模板選 'Edit Cloudflare Workers' 或自建含 Pages:Edit）"
fi

# 2) Egress preflight
say "→ 預檢 egress：$API_HOST"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
  "https://$API_HOST/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" 2>&1 || true)"
case "$code" in
  200) say "  ✓ token 有效、網路通" ;;
  000|"") fail "連不到 $API_HOST（egress 被擋）。
    網頁環境：Settings → Network access → Custom → 加入：
        $API_HOST
        *.cloudflare.com
    存檔後開新 session 再跑。" ;;
  403)
    body="$(curl -sS --max-time 15 "https://$API_HOST/client/v4/user/tokens/verify" \
            -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" 2>&1 || true)"
    if printf '%s' "$body" | grep -qi 'not in allowlist'; then
      fail "egress 防火牆擋掉 $API_HOST。加 $API_HOST + *.cloudflare.com 到 allowlist 再跑。"
    fi
    fail "token 被 Cloudflare 拒絕（HTTP 403）。確認 token 權限含 Account · Cloudflare Pages · Edit。" ;;
  401) fail "token 無效（HTTP 401）。重簽一把含 Pages:Edit 的 token。" ;;
  *)   say "  ⚠ 預檢回 HTTP $code，仍嘗試部署…" ;;
esac

# 3) Sanity: index.html exists (deploying the wrong folder is the #1 mistake)
[ -f index.html ] || fail "index.html 不見了。你是不是在錯的目錄跑？應該在 tigerline-web/"
[ -d js ] || fail "js/ 資料夾不見了。"
[ -d css ] || fail "css/ 資料夾不見了。"

# 4) Run the JS parity tests before deploying — same rule SID uses (must-green gate).
if command -v node >/dev/null 2>&1; then
  say "→ 跑 17 個 parity test 前置檢查…"
  if ! node --test tests/*.test.js >/dev/null 2>&1; then
    say "  ⚠ 有 test 沒過。先修再部署。跑 node --test tests/*.test.js 看細節。"
    exit 1
  fi
  say "  ✓ 17/17 綠"
fi

# 5) Deploy — Cloudflare Pages serves this directory verbatim, no build step.
say "→ wrangler pages deploy（專案：$PROJECT）"
npx --yes wrangler@latest pages deploy . \
  --project-name "$PROJECT" \
  --commit-dirty=true \
  --branch=main

say ""
say "✅ 部署完成 → https://$PROJECT.pages.dev"
say ""
say "存進手機書籤 = 完成。之後改完 code，同樣的指令再跑一次就好（30 秒完成）。"
