#!/bin/bash
# Re-installs user-scope Claude Code CLI plugins on every fresh remote environment.
# Plugin installs live in ~/.claude (user scope), which does NOT survive an
# ephemeral remote container being recycled — so this re-syncs them each start.
set -uo pipefail

command -v claude >/dev/null 2>&1 || { echo "⚠️ claude CLI not found — skipping plugin sync"; exit 0; }

PLUGINS=(
  "impeccable@impeccable|pbakaus/impeccable"
  "claude-mem@thedotmack|thedotmack/claude-mem"
  "superpowers@superpowers-marketplace|obra/superpowers-marketplace"
)

installed_list="$(claude plugin list 2>/dev/null)"
ready=0

for entry in "${PLUGINS[@]}"; do
  plugin_spec="${entry%%|*}"
  marketplace_repo="${entry##*|}"

  if echo "$installed_list" | grep -q "$plugin_spec"; then
    ready=$((ready + 1))
    continue
  fi

  claude plugin marketplace add "$marketplace_repo" >/dev/null 2>&1
  if claude plugin install "$plugin_spec" >/dev/null 2>&1; then
    ready=$((ready + 1))
  fi
done

total=${#PLUGINS[@]}
if [ "$ready" -eq "$total" ]; then
  echo "✅ Plugins ready ($ready/$total): impeccable, claude-mem, superpowers"
else
  echo "⚠️ Plugins $ready/$total ready — run 'claude plugin list' to check"
fi
