#!/usr/bin/env bash
# Auto-Montage demo — zero-dependency (ffmpeg only) bilingual-subtitle vertical short.
# Synthesizes footage, burns aligned 中/EN subtitles, renders an mp4, then runs the
# subtitle gate to prove the output passes. No OpenMontage, no API keys, no GUI.
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

# Bilingual subtitle track (zh on top, EN below). Timings respect the gate's CPS/line limits.
cat > "$SRT" <<'EOF'
1
00:00:00,000 --> 00:00:03,000
招牌紅燒牛肉麵
Signature Braised Beef Noodles

2
00:00:03,200 --> 00:00:06,500
湯頭熬煮八小時
Broth simmered for eight hours

3
00:00:06,700 --> 00:00:09,500
手工拉麵 現點現煮
Hand-pulled, made to order

4
00:00:09,700 --> 00:00:12,000
今天就來嚐一碗
Come taste a bowl today
EOF

# 9:16 warm animated background + centered brand + burned bilingual subtitles.
ffmpeg -y -loglevel error \
  -f lavfi -i "gradients=s=1080x1920:c0=0x2b1505:c1=0xb5571a:x0=0:y0=0:x1=1080:y1=1920:d=12:speed=0.012" \
  -f lavfi -t 12 -i "sine=frequency=220:sample_rate=44100" \
  -vf "drawbox=x=0:y=1500:w=1080:h=420:color=black@0.35:t=fill,\
drawtext=fontfile='$FONT':text='好味牛肉麵':fontsize=64:fontcolor=white@0.92:x=(w-text_w)/2:y=140:shadowcolor=black@0.6:shadowx=2:shadowy=2,\
subtitles='$SRT':force_style='FontName=$FONTNAME,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=90'" \
  -c:v libx264 -pix_fmt yuv420p -profile:v high -preset medium -r 30 \
  -c:a aac -b:a 128k -shortest "$MP4"

echo "Rendered: $MP4 ($(du -h "$MP4" | cut -f1))"

# Prove the burned subtitle track passes the gate.
echo "== subtitle gate =="
python3 "$HERE/../scripts/subtitle_align_check.py" "$SRT" --lang zh \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('PASS' if d['ok'] else 'FAIL','cues=',d['cues'],'critical=',d['critical'])"
