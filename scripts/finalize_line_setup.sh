#!/bin/sh
# One-shot LINE launch setup against an already-deployed Render service.
#
# Run this from YOUR OWN machine (or anywhere with normal internet access —
# NOT from a sandboxed dev environment that blocks outbound calls to LINE).
# It logs into the admin console, then calls the one admin endpoint that
# registers the LIFF app, uploads the rich menu, and sets the webhook
# endpoint URL — all via LINE's own APIs, replacing three manual console
# sessions with one HTTP call.
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

echo "2) POST /admin/line/finalize (LIFF + rich menu + webhook endpoint, one call) ..."
FINALIZE_RESULT=$(curl -sS -b "$COOKIE_JAR" -X POST "$BASE_URL/admin/line/finalize" \
  -H "Content-Type: application/json" -d '{}')
echo "$FINALIZE_RESULT"
echo

echo "Done. The \"status\" block inside the result above is the single source of"
echo "truth: if \"ready\":true, LIFF + rich menu + webhook endpoint + secret are"
echo "all in place. If ready is false and the notes mention \"Use webhook\", the"
echo "endpoint URL is already set correctly by this script — the only thing left"
echo "is flipping the \"Use webhook\" toggle on in LINE Developers Console (LINE"
echo "has no public API for that one switch) — see COMMANDER_HANDOFF.md."
