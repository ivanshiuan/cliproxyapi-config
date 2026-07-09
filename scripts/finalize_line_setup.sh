#!/bin/sh
# One-shot LINE launch setup against an already-deployed Render service.
#
# Run this from YOUR OWN machine (or anywhere with normal internet access —
# NOT from a sandboxed dev environment that blocks outbound calls to LINE).
# It logs into the admin console, then calls the two admin endpoints that
# register the LIFF app and upload the rich menu via LINE's own APIs —
# replacing what used to be two manual console sessions.
#
# Usage:
#   RESTO_ADMIN_PASSCODE=xxxx ./scripts/finalize_line_setup.sh https://chouhutiger.onrender.com
set -eu

BASE_URL="${1:?usage: $0 <base_url> (e.g. https://chouhutiger.onrender.com)}"
PASSCODE="${RESTO_ADMIN_PASSCODE:?set RESTO_ADMIN_PASSCODE env var to your admin passcode first}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

echo "1) logging into $BASE_URL/admin/login ..."
curl -sS -f -c "$COOKIE_JAR" -X POST "$BASE_URL/admin/login" \
  -H "Content-Type: application/json" \
  -d "{\"passcode\":\"$PASSCODE\"}" > /dev/null
echo "   ok."

echo "2) POST /admin/line/liff ..."
LIFF_RESULT=$(curl -sS -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/line/liff" \
  -H "Content-Type: application/json" -d '{}')
echo "$LIFF_RESULT"
echo

echo "3) POST /admin/line/richmenu ..."
RICHMENU_RESULT=$(curl -sS -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/line/richmenu" \
  -H "Content-Type: application/json" -d '{}')
echo "$RICHMENU_RESULT"
echo

echo "4) GET /admin/line/status (launch-readiness summary) ..."
STATUS_RESULT=$(curl -sS -b "$COOKIE_JAR" "$BASE_URL/admin/line/status")
echo "$STATUS_RESULT"
echo

echo "Done. The status block above is the single source of truth: if \"ready\":true,"
echo "LIFF + rich menu + webhook secret are all in place. The only remaining"
echo "console step is turning on the Webhook URL ($BASE_URL/line/webhook) in"
echo "LINE Developers Console so the auto-welcome fires — see COMMANDER_HANDOFF.md."
