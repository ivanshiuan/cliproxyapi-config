#!/usr/bin/env bash
# Auto-Montage demo — zero-dependency (ffmpeg only) bilingual-subtitle vertical short.
# Synthesizes footage, burns aligned 中/EN subtitles, renders an mp4, then runs the
# subtitle gate to prove the output passes. No OpenMontage, no API keys, no GUI.
#
# Brand: 周霸虎老火鍋（重慶火鍋）— swap this for any restaurant by editing SRT + drawtext below.
#
# Usage: bash examples/demo_bilingual_short.sh [out_dir]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/out}"
mkdir -p "$OUT"
SRT="$OUT/demo.srt"
MP4="$OUT/demo_bilingual_short.mp4"

# Pick an available CJK-capable font. FONT = file (for drawtext); FONTNAME = fontconfig
# family (for libass/subtitles). They must match or burned subtitles render as tofu.
FONT=""; FONTNAME=""
pick() { if [ -z "$FONT" ] && [ -f "$1" ]; then FONT="$1"; FONTNAME="$2"; fi; return 0; }
pick /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc "Noto Sans CJK TC"
pick /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc "WenQuanYi Zen Hei"
pick /System/Library/Fonts/PingFang.ttc "PingFang TC"
[ -n "$FONT" ] || { echo "No CJK font found; install fonts-noto-cjk."; exit 1; }
echo "Using font: $FONTNAME ($FONT)"

# Bilingual subtitle track — 重慶火鍋版。
# All timings respect CPS/line limits: CJK ≤9 cps / ≤15 chars, Latin ≤17 cps / ≤42 chars.
cat > "$SRT" <<'EOF'
1
00:00:00,000 --> 00:00:03,000
正宗重慶老火鍋
Authentic Chongqing Hot Pot

2
00:00:03,200 --> 00:00:06,500
牛油紅湯 香辣醇厚
Rich Tallow Broth — Bold and Spicy

3
00:00:06,700 --> 00:00:09,500
鮮毛肚 鵝腸 小酥肉
Tripe · Goose Intestine · Crispy Pork

4
00:00:09,700 --> 00:00:12,000
今晚來一鍋 回味無窮
One Hot Pot Tonight — Unforgettable
EOF

# 9:16 dark ember gradient (重慶火鍋美學) + two-line brand name + burned bilingual subtitles.
# Background: charcoal-black top → deep crimson bottom, slow drift animation.
ffmpeg -y -loglevel error \
  -f lavfi -i "gradients=s=1080x1920:c0=0x100302:c1=0x6b1200:x0=540:y0=0:x1=540:y1=1920:d=12:speed=0.010" \
  -f lavfi -t 12 -i "sine=frequency=180:sample_rate=44100" \
  -vf "drawbox=x=0:y=1530:w=1080:h=390:color=black@0.45:t=fill,\
drawbox=x=80:y=288:w=920:h=2:color=0xd05010@0.40:t=fill,\
drawtext=fontfile='$FONT':text='周霸虎':fontsize=92:fontcolor=white@0.95:x=(w-text_w)/2:y=92:shadowcolor=0x6b120080:shadowx=3:shadowy=4,\
drawtext=fontfile='$FONT':text='老火鍋':fontsize=72:fontcolor=0xffd080:x=(w-text_w)/2:y=200:shadowcolor=0x5a080060:shadowx=2:shadowy=3,\
subtitles='$SRT':force_style='FontName=$FONTNAME,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=90'" \
  -c:v libx264 -pix_fmt yuv420p -profile:v high -preset medium -r 30 \
  -c:a aac -b:a 128k -shortest "$MP4"

echo "Rendered: $MP4 ($(du -h "$MP4" | cut -f1))"

# Prove the burned subtitle track passes the gate.
echo "== subtitle gate =="
python3 "$HERE/../scripts/subtitle_align_check.py" "$SRT" --lang zh \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('PASS' if d['ok'] else 'FAIL','cues=',d['cues'],'critical=',d['critical'])"
