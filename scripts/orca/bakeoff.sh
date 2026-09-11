#!/usr/bin/env bash
# bakeoff.sh — 一鍵：同一 spec 餵多個 agent 各自 worktree 平行實作 → 產出比稿報告
#
# 用法（經 make）：
#   make bakeoff SPEC=specs/profit_calc.md             # 預設 lanes：devswarm + claude
#   make bakeoff SPEC=... LANES="devswarm"             # 只跑蜂群 lane
#   make bakeoff SPEC=... FRESH=1                      # 全部 lane 重建
#
# Lanes：
#   devswarm — 五角色蜂群（PM→Architect→Coder→Reviewer→QA），走 swarm_worktree.sh
#   claude   — Claude Code CLI headless 直接實作（需本機裝 claude CLI）
#
# 產出：docs/knowledge/<今天>-bakeoff-<slug>.md 比稿報告（idempotent 覆寫）。
# 指揮官 approval 點：讀報告 → 挑贏家分支 → 審該分支的 PR。
#
# 預算紅線：只有 S 級 spec（錢/帳/法遵）值得比稿 — 見 docs/20 §五。

set -euo pipefail

SPEC="${1:-}"
LANES="${LANES:-devswarm claude}"
BUDGET="${BUDGET:-5.0}"
FRESH="${FRESH:-0}"

die() { echo "❌ $*" >&2; exit 1; }
info() { echo "· $*"; }

[ -n "$SPEC" ] || die "usage: bakeoff.sh specs/<name>.md（或 make bakeoff SPEC=...）"

ROOT="$(git rev-parse --show-toplevel)"
SCRIPT_DIR="$ROOT/scripts/orca"
SLUG="$(basename "$SPEC" .md)"
TODAY="$(date +%Y-%m-%d)"
REPORT="$ROOT/docs/knowledge/$TODAY-bakeoff-$SLUG.md"
BASE_REF="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"

RESULTS=""   # "lane:branch:status" 空白分隔

# ── 各 lane 執行（循序；平行版等 lane 穩定後再開）─────────────────────────
for LANE in $LANES; do
  BRANCH="bakeoff/$SLUG-$LANE"
  WT_NAME="bakeoff-$SLUG-$LANE"
  WT_DIR="$ROOT/.worktrees/$WT_NAME"
  echo
  echo "════ lane: $LANE（分支 $BRANCH）════"

  case "$LANE" in
    devswarm)
      set +e
      WT_NAME="$WT_NAME" BRANCH="$BRANCH" FRESH="$FRESH" BUDGET="$BUDGET" \
        bash "$SCRIPT_DIR/swarm_worktree.sh" "$SPEC"
      ST=$?
      set -e
      if [ $ST -eq 0 ]; then RESULTS="$RESULTS $LANE:$BRANCH:pass"; else RESULTS="$RESULTS $LANE:$BRANCH:fail"; fi
      ;;

    claude)
      command -v claude >/dev/null 2>&1 || { info "claude CLI 不在 PATH — 跳過此 lane"; RESULTS="$RESULTS $LANE:$BRANCH:skipped"; continue; }
      # worktree 準備（重用 swarm_worktree 的 SETUP_ONLY 模式，保持單一實作）
      WT_NAME="$WT_NAME" BRANCH="$BRANCH" FRESH="$FRESH" SETUP_ONLY=1 \
        bash "$SCRIPT_DIR/swarm_worktree.sh" "$SPEC"
      info "Claude Code headless 實作中（產出 + 測試 + 自跑 gate）..."
      set +e
      ( cd "$WT_DIR" && claude -p "$(cat <<EOF
照 spec 檔 $SPEC 實作：單一 module 放 restaurant_api/services/、單一測試放 tests/services/。
遵守 CLAUDE.md 不變法則（Decimal、tz-aware、DomainError、audit_service）。
完成後跑 .venv 的 ruff check 與該測試檔 pytest，全綠才收工；然後 git add 相關檔案並 commit（feat(services): 前綴）。
EOF
)" --permission-mode acceptEdits )
      ST=$?
      set -e
      if [ $ST -eq 0 ]; then RESULTS="$RESULTS $LANE:$BRANCH:pass"; else RESULTS="$RESULTS $LANE:$BRANCH:fail"; fi
      ;;

    *)
      die "未知 lane：$LANE（支援 devswarm / claude）"
      ;;
  esac
done

# ── 比稿報告（idempotent 覆寫同名檔）──────────────────────────────────────
echo
info "產出比稿報告 → ${REPORT#"$ROOT"/}"
{
  echo "---"
  echo "source_type: 其他"
  echo "source_url: (internal bakeoff)"
  echo "captured_at: $TODAY"
  echo "tags: [bakeoff, DevSwarm, agent戰力]"
  echo "applies_to: [系統與AI]"
  echo "status: inbox"
  echo "---"
  echo
  echo "# Bakeoff — $SLUG（$TODAY）"
  echo
  echo "同一 spec（\`$SPEC\`）多 agent 平行實作的比稿結果。基準分支：\`$BASE_REF\`。"
  echo
  echo "## 各 lane 結果"
  echo
  echo "| lane | 分支 | 狀態 | diff 規模 |"
  echo "|---|---|---|---|"
  for R in $RESULTS; do
    L="${R%%:*}"; REST="${R#*:}"; B="${REST%%:*}"; S="${REST##*:}"
    if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$B"; then
      STAT="$(git -C "$ROOT" diff --shortstat "$BASE_REF...$B" 2>/dev/null || echo 'n/a')"
      STAT="${STAT:-（無變更）}"
    else
      STAT="（無分支）"
    fi
    echo "| $L | \`$B\` | $S | $STAT |"
  done
  echo
  echo "## 審查指令（在主 repo 跑）"
  echo
  echo '```bash'
  for R in $RESULTS; do
    REST="${R#*:}"; B="${REST%%:*}"
    echo "git diff $BASE_REF...$B    # ${R%%:*} 的完整 diff"
  done
  echo '```'
  echo
  echo "## 裁判評語（rubric：Codex 不變法則逐條 + spec AC 逐條 + 測試綠）"
  echo
  echo "> 待填：用一個乾淨 Claude session 當獨立裁判（不看實作過程、只看 diff），"
  echo "> 或在 Orca 開 review 佇列逐條比。填完把本卡 status 改 reviewed。"
  echo
  echo "## 判決"
  echo
  echo "- 贏家 lane：＿＿＿"
  echo "- 理由（引用法則 id / AC 編號）：＿＿＿"
  echo "- 輸家 worktree 處置：\`make wt-clean\`（分支保留供事後考古）"
} > "$REPORT"

echo
echo "✅ bakeoff 完成。指揮官 approval 流程："
echo "   1. 讀報告：${REPORT#"$ROOT"/}"
echo "   2. 挑贏家 → push 該分支開 PR（或叫 Claude：「push bakeoff 贏家 <lane>」）"
echo "   3. make wt-clean 清場"
