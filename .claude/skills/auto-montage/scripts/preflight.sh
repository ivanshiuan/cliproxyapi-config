#!/usr/bin/env bash
# Auto-Montage preflight — 偵測引擎與工具，印出能力邊界。原創，不含上游程式碼。
# 用法: bash preflight.sh
set -uo pipefail

ok=0; warn=0; fail=0
green(){ printf '  \033[32m✓\033[0m %s\n' "$1"; ok=$((ok+1)); }
yellow(){ printf '  \033[33m!\033[0m %s\n' "$1"; warn=$((warn+1)); }
red(){ printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }

ver_ge(){ # ver_ge HAVE MIN  -> 0 if HAVE>=MIN
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

echo "== Auto-Montage Preflight =="

# --- 1. 系統工具 ---
echo "[1] 系統需求"
if command -v python3 >/dev/null 2>&1; then
  pv=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0)
  if ver_ge "$pv" "3.10"; then green "python3 $pv (>=3.10)"; else red "python3 $pv < 3.10，OpenMontage 需要 3.10+"; fi
else red "找不到 python3"; fi

if command -v node >/dev/null 2>&1; then
  nv=$(node -v 2>/dev/null | sed 's/^v//'); nmaj=${nv%%.*}
  if [ "${nmaj:-0}" -ge 18 ] 2>/dev/null; then green "node v$nv (>=18)"; else red "node v$nv < 18，Remotion/HyperFrames 需要 18+"; fi
else yellow "找不到 node（Remotion/HyperFrames 合成會不可用；純 ffmpeg 路線仍可）"; fi

command -v ffmpeg  >/dev/null 2>&1 && green "ffmpeg"  || red "找不到 ffmpeg（後製必需）"
command -v ffprobe >/dev/null 2>&1 && green "ffprobe" || red "找不到 ffprobe（時長/靜音偵測必需）"

# --- 2. 定位 OpenMontage ---
echo "[2] 執行引擎 OpenMontage"
HOME_DIR=""
for c in "${OPENMONTAGE_HOME:-}" "./OpenMontage" "../OpenMontage"; do
  [ -n "$c" ] && [ -f "$c/tools/tool_registry.py" ] && { HOME_DIR="$c"; break; }
done
if [ -z "$HOME_DIR" ]; then
  red "找不到 OpenMontage（設 OPENMONTAGE_HOME 或放在 ./OpenMontage、../OpenMontage）"
  echo "      安裝: git clone https://github.com/calesthio/OpenMontage.git && cd OpenMontage && make setup"
else
  green "OpenMontage @ $HOME_DIR"
fi

# --- 3. 能力選單（引擎在才跑）---
echo "[3] 能力選單"
if [ -n "$HOME_DIR" ] && command -v python3 >/dev/null 2>&1; then
  if ( cd "$HOME_DIR" && python3 -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))" ) 2>/tmp/_am_pf.err; then
    : # 上面已直接印出 JSON
  else
    yellow "能力選單跑不起來（可能還沒 make setup）。錯誤："
    sed 's/^/      /' /tmp/_am_pf.err 2>/dev/null | tail -n 8
  fi
  rm -f /tmp/_am_pf.err
else
  yellow "略過（引擎或 python3 不在）"
fi

echo "== 結果: $ok ok / $warn warn / $fail fail =="
[ "$fail" -eq 0 ] || { echo "有 critical 缺項，先補齊再生成。"; exit 1; }
echo "可以開始。預設走零金鑰免費基線；要動付費供應商先報價。"
