#!/usr/bin/env python3
"""Auto-Montage 雙語字幕對齊/可讀性閘（原創，不含上游程式碼）。

檢查一份 .srt（或中英雙檔）是否符合可讀性與對齊紀律，輸出 JSON 報告。
有 critical 問題時以非零碼結束 → CI / Skill 可據此擋下不合格成片。

用法:
  python subtitle_align_check.py subs.srt [--lang zh|en|auto]
  python subtitle_align_check.py zh.srt --pair en.srt        # 雙檔條數/時間對齊
門檻（可調）:
  CJK: CPS<=9, 單行<=15 字     Latin: CPS<=17, 單行<=42 字元
  每段停留 0.8s~7s, 相鄰不可重疊, 每段<=2 行
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field

LIMITS = {
    "cjk": {"cps": 9.0, "line_chars": 15},
    "latin": {"cps": 17.0, "line_chars": 42},
    "min_dur": 0.8,
    "max_dur": 7.0,
    "max_lines": 2,
    "min_gap_s": 0.0,  # 重疊 = 負 gap，視為 critical
}

TS = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


@dataclass
class Cue:
    idx: int
    start: float
    end: float
    text: str
    lines: list[str] = field(default_factory=list)

    @property
    def dur(self) -> float:
        return self.end - self.start


def _to_sec(h, m, s, ms):
    ms = (ms + "00")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: str) -> list[Cue]:
    with open(path, encoding="utf-8-sig") as fh:
        raw = fh.read()
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        ts_line = next((ln for ln in lines if TS.search(ln)), None)
        if not ts_line:
            continue
        m = TS.search(ts_line)
        start = _to_sec(*m.group(1, 2, 3, 4))
        end = _to_sec(*m.group(5, 6, 7, 8))
        ti = lines.index(ts_line)
        idx_val = 0
        if ti > 0 and lines[0].strip().isdigit():
            idx_val = int(lines[0].strip())
        text_lines = lines[ti + 1:]
        cues.append(
            Cue(idx_val or len(cues) + 1, start, end, " ".join(text_lines), text_lines)
        )
    return cues


def is_cjk(ch: str) -> bool:
    if not ch.strip():
        return False
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return False
    return any(k in name for k in ("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))


def script_of(text: str) -> str:
    cjk = sum(1 for c in text if is_cjk(c))
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    return "cjk" if cjk >= latin else "latin"


def visible_len(line: str, kind: str) -> int:
    if kind == "cjk":
        return sum(1 for c in line if not c.isspace())
    return len(line.strip())


def check(cues: list[Cue], lang: str) -> list[dict]:
    findings: list[dict] = []

    def add(sev, cue, msg):
        findings.append({"severity": sev, "cue": cue, "msg": msg})

    if not cues:
        add("critical", 0, "解析不到任何字幕段")
        return findings

    for i, c in enumerate(cues):
        kind = lang if lang in ("cjk", "latin") else script_of(c.text)
        lim = LIMITS[kind]
        # 時長
        if c.dur < LIMITS["min_dur"]:
            add("critical", c.idx, f"停留過短 {c.dur:.2f}s < {LIMITS['min_dur']}s")
        if c.dur > LIMITS["max_dur"]:
            add("should", c.idx, f"停留過長 {c.dur:.2f}s > {LIMITS['max_dur']}s")
        if c.end <= c.start:
            add("critical", c.idx, "結束時間 <= 開始時間")
        # CPS
        n = visible_len(c.text.replace(" ", "") if kind == "cjk" else c.text, kind)
        if c.dur > 0:
            cps = n / c.dur
            if cps > lim["cps"]:
                add("critical", c.idx, f"CPS 過快 {cps:.1f} > {lim['cps']} ({kind})")
        # 行數 / 行長
        if len(c.lines) > LIMITS["max_lines"]:
            add("should", c.idx, f"超過 {LIMITS['max_lines']} 行（{len(c.lines)} 行）")
        for ln in c.lines:
            vl = visible_len(ln, kind)
            if vl > lim["line_chars"]:
                add("critical", c.idx, f"單行過長 {vl} > {lim['line_chars']} ({kind}): {ln[:30]}…")
        # 重疊 / 間隙
        if i > 0:
            gap = c.start - cues[i - 1].end
            if gap < 0:
                add("critical", c.idx, f"與前段重疊 {gap:.2f}s")
            elif gap < LIMITS["min_gap_s"]:
                add("should", c.idx, f"間隙過小 {gap:.2f}s")
    return findings


def check_pair(a: list[Cue], b: list[Cue]) -> list[dict]:
    out = []
    if len(a) != len(b):
        out.append({"severity": "should", "cue": 0,
                    "msg": f"雙語條數不一致 {len(a)} vs {len(b)}"})
    for i in range(min(len(a), len(b))):
        ds = abs(a[i].start - b[i].start)
        de = abs(a[i].end - b[i].end)
        if ds > 0.25 or de > 0.25:
            out.append({"severity": "should", "cue": a[i].idx,
                        "msg": f"時間碼未對齊 Δstart={ds:.2f}s Δend={de:.2f}s"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("srt")
    ap.add_argument("--lang", default="auto", choices=["auto", "zh", "en", "cjk", "latin"])
    ap.add_argument("--pair", help="另一語言的 .srt，檢查條數/時間對齊")
    args = ap.parse_args()

    lang = {"zh": "cjk", "en": "latin"}.get(args.lang, args.lang)
    try:
        cues = parse_srt(args.srt)
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error": f"找不到檔案: {args.srt}"}, ensure_ascii=False))
        return 2

    findings = check(cues, lang)
    if args.pair:
        findings += check_pair(cues, parse_srt(args.pair))

    crit = [f for f in findings if f["severity"] == "critical"]
    should = [f for f in findings if f["severity"] == "should"]
    report = {
        "ok": len(crit) == 0,
        "file": args.srt,
        "cues": len(cues),
        "critical": len(crit),
        "should": len(should),
        "findings": findings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not crit else 1


if __name__ == "__main__":
    sys.exit(main())
