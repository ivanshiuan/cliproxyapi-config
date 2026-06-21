#!/usr/bin/env bash
# SID 一鍵部署到 Cloudflare Pages — 含 token / egress 預檢與明確錯誤指引
# 用法：PROJECT=498-win ./deploy.sh   （或 make deploy PROJECT=498-win）
set -euo pipefail

PROJECT="${PROJECT:-498-win}"
API_HOST="api.cloudflare.com"

cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
fail() { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

# 1) token 在不在
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  fail "缺少 CLOUDFLARE_API_TOKEN。請把它加進這個 web 環境的 Environment variables，再開新 session。"
fi

# 2) egress 預檢：能不能連到 Cloudflare API
say "→ 預檢 egress：$API_HOST"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
  "https://$API_HOST/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" 2>&1 || true)"
case "$code" in
  200) say "  ✓ token 有效、網路通" ;;
  000|"") fail "連不到 $API_HOST（egress 被擋）。請到環境設定 → Network access → Custom → 加入：
        $API_HOST
        *.cloudflare.com
   存檔後開新 session 再跑。" ;;
  403)
    # 區分「防火牆擋」與「Cloudflare 拒絕」
    body="$(curl -sS --max-time 15 "https://$API_HOST/client/v4/user/tokens/verify" \
            -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" 2>&1 || true)"
    if printf '%s' "$body" | grep -qi 'not in allowlist'; then
      fail "egress 防火牆擋掉 $API_HOST。環境設定 → Network access → Custom → 加入 $API_HOST 與 *.cloudflare.com，存檔後開新 session。"
    fi
    fail "token 被 Cloudflare 拒絕（HTTP 403）。請確認 token 權限含 Account · Cloudflare Pages · Edit。" ;;
  401) fail "token 無效（HTTP 401）。請重簽一把含 Pages:Edit 的 token。" ;;
  *)   say "  ⚠ 預檢回 HTTP $code，仍嘗試部署…" ;;
esac

# 3) build
say "→ build dist/site/"
node build.js >/dev/null

# 4) deploy
say "→ wrangler pages deploy（專案：$PROJECT）"
npx --yes wrangler@latest pages deploy dist/site \
  --project-name "$PROJECT" \
  --commit-dirty=true

say ""
say "✅ 部署完成 → https://$PROJECT.pages.dev"
