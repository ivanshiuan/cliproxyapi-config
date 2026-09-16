#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["yt-dlp>=2024.0.0"]
# ///
"""把一支影片（本機檔或 YouTube 連結）拆成「畫面幀 + 逐字稿」，給 Claude 看。

為什麼存在
----------
Claude Code 的原生 `Read` 讀得了單張圖片，但讀不了影片。這支腳本就是「先轉一手」：
用 ffmpeg 從影片**低頻抽樣**出一疊時間戳幀圖（預設最多 100 張），能拿到逐字稿就一起拿，
再由 `/watch-video` 指令叫 Claude 逐幀 Read + 對照逐字稿回答問題。

設計原則（呼應 CLAUDE.md 不變法則 + to_md.py 前例）
----------------------------------------------------
- **零依賴污染**：yt-dlp 不進 pyproject.toml。本檔用 PEP 723 inline metadata，
  由 `uv run` 起一個快取的臨時環境跑，碰不到 .venv / production stack。
- **系統工具外掛**：ffmpeg / ffprobe 是系統二進位（`brew install ffmpeg`），
  不是 Python 套件；缺了就明講怎麼裝，不硬 import。
- **成本自覺**：長片抽的幀多、token 燒得凶。預設封頂 100 幀，先看結構再鑽細節。
- **逐字稿分層**：YouTube 直接抓字幕（便宜）；本機檔要 ASR 得 `--transcribe`（Whisper，較重、選配）。
- **可稽核**：只做「影片 → 幀 + 文字」。任何抽出來的價格/數字（例：對手菜單價目板）
  只是參考，要入帳仍走 restaurant_api 結構化驗證 + 人工覆核，別直接信這支吐的純文字。

用法
----
    uv run scripts/watch_video.py ~/Downloads/demo.mp4        # 本機檔，抽幀
    uv run scripts/watch_video.py 'https://youtu.be/xxxx'      # YouTube，抽幀 + 抓字幕
    uv run scripts/watch_video.py demo.mp4 --frames 24         # 只抽 24 幀（先看結構）
    uv run scripts/watch_video.py demo.mp4 --start 90 --end 105 --fps 2 --width 1280
                                                               # 聚焦 1:30–1:45，高解析、每秒 2 幀
    uv run --with faster-whisper scripts/watch_video.py demo.mp4 --transcribe
                                                               # 本機檔跑 Whisper 逐字稿

    # 或透過 Makefile：
    make watch-video FILE=~/Downloads/demo.mp4

產出（預設寫到 <影片>.watch/ 或 ./<video-id>.watch/）
    frames/0001_00-00-05.jpg ...   # 時間戳幀圖，給 Claude 逐張 Read
    transcript.txt                 # 逐字稿（有的話），每行 [MM:SS] 前綴
    manifest.json                  # 機器可讀索引：來源、時長、每幀時間戳、路徑

退出碼：成功 0；失敗 1。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 硬上限：擋住「長片 × 高 fps」不小心抽出幾千張幀把 token 燒光。
# 要更多請自己分段抽，或把這個數字調高（但先想清楚成本）。
HARD_MAX_FRAMES = 400
DEFAULT_TARGET_FRAMES = 100
# 預設抽樣封頂 1 fps（低頻抽樣）：短片自然抽得少（不硬湊到 100），
# 長片才靠「總幀數 100」把頻率壓更低。明確給 --frames/--fps 時不受此限。
DEFAULT_MAX_FPS = 1.0
DEFAULT_WIDTH = 640
# YouTube 字幕語言偏好（台灣優先），yt-dlp 會挑有的那個。
DEFAULT_SUB_LANGS = "zh-Hant,zh-TW,zh-HK,zh,en,en-US"


# ── 小工具 ────────────────────────────────────────────────────────────────


def _eprint(msg: str) -> None:
    """狀態訊息一律走 stderr，讓 stdout 保持乾淨（給管線/擷取用）。"""
    print(msg, file=sys.stderr)


def _fmt_ts(seconds: float) -> str:
    """秒數 → HH-MM-SS（給檔名用，冒號在檔名不安全所以用連字號）。"""
    seconds = max(0, round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}-{m:02d}-{s:02d}"


def _fmt_label(seconds: float) -> str:
    """秒數 → MM:SS 或 H:MM:SS（給人/Claude 看的時間標籤）。"""
    seconds = max(0, round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _require(binary: str, install_hint: str) -> str:
    """確認系統二進位存在，回傳絕對路徑；缺了就丟 RuntimeError 附安裝指引。"""
    path = shutil.which(binary)
    if not path:
        raise RuntimeError(f"缺少系統工具 `{binary}`。安裝方式：{install_hint}")
    return path


def _ffmpeg_paths() -> tuple[str, str]:
    """回傳 (ffmpeg, ffprobe) 路徑，缺任一就報清楚。"""
    hint = "macOS `brew install ffmpeg`；Debian/Ubuntu `sudo apt install ffmpeg`"
    return _require("ffmpeg", hint), _require("ffprobe", hint)


def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


# ── 影片時長 ──────────────────────────────────────────────────────────────


def _probe_duration(ffprobe: str, video: Path) -> float:
    """用 ffprobe 讀影片秒數；container 沒寫就退而求其次讀 video stream。"""
    for entry in ("format=duration", "stream=duration"):
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0" if entry.startswith("stream") else "0",
            "-show_entries",
            entry,
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
        try:
            out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
        except subprocess.CalledProcessError:
            continue
        # 可能多行（多 stream），取第一個能 parse 的正數
        for line in out.splitlines():
            line = line.strip()
            try:
                val = float(line)
            except ValueError:
                continue
            if val > 0:
                return val
    raise RuntimeError(f"讀不到影片時長（ffprobe 失敗）：{video}。檔案可能損壞或不是影片。")


# ── 抽樣頻率計算 ──────────────────────────────────────────────────────────


def _compute_fps(
    window: float, explicit_fps: float | None, target_frames: int | None
) -> tuple[float, int]:
    """算出抽樣 fps 與預估幀數。

    優先序：
      --fps F           → 固定 F fps（照給）。
      --frames N        → 剛好抽 N 張（fps = N / window，不受 1 fps 封頂）。
      皆未給（預設）    → 低頻抽樣：min(1 fps, 100 幀 / window)，短片自然少、長片壓到 100。
    回傳 (sample_fps, est_frames)；est_frames 一定 ≥ 1、≤ HARD_MAX_FRAMES（超過丟錯）。
    """
    if window <= 0:
        return 1.0, 1

    if explicit_fps is not None:
        if explicit_fps <= 0:
            raise ValueError("--fps 必須 > 0")
        fps = explicit_fps
    elif target_frames is not None:
        if target_frames < 1:
            raise ValueError("--frames 必須 ≥ 1")
        # 明確要 N 張 → 在 window 內平均鋪開
        fps = target_frames / window
    else:
        # 預設：低頻抽樣，最多 1 fps，且總幀數不超過 DEFAULT_TARGET_FRAMES
        fps = min(DEFAULT_MAX_FPS, DEFAULT_TARGET_FRAMES / window)

    est = max(1, round(fps * window))
    if est > HARD_MAX_FRAMES:
        raise RuntimeError(
            f"預估會抽出 {est} 幀，超過硬上限 {HARD_MAX_FRAMES}。"
            f"請縮小 --start/--end 範圍、調低 --fps，或明確指定較小的 --frames。"
        )
    return fps, est


# ── 幀抽取（ffmpeg 單趟 + showinfo 精準時間戳）─────────────────────────────


_PTS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")


def _extract_frames(
    ffmpeg: str,
    video: Path,
    frames_dir: Path,
    *,
    start: float,
    fps: float,
    width: int,
) -> list[tuple[float, Path]]:
    """單趟 ffmpeg 抽幀，回傳 [(absolute_seconds, frame_path), ...] 依時間排序。

    用 showinfo filter 解析每張輸出幀的 pts_time（相對 -ss 起點），
    加回 start 得到絕對時間；解析失敗則退回「起點 + n/fps」估算。
    幀檔先寫成 f%04d.jpg，之後改名成 0001_HH-MM-SS.jpg（時間戳嵌檔名，方便 Claude 對照）。
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    # 抽樣 + 縮寬（高度自動、保持偶數）+ showinfo 印時間戳
    vf = f"fps={fps:.6f},scale={width}:-2,showinfo"
    tmp_pattern = str(frames_dir / "f%04d.jpg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]  # input seeking，快
    cmd += ["-i", str(video), "-vf", vf, "-vsync", "vfr", "-q:v", "3", tmp_pattern]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 抽幀失敗（returncode={proc.returncode}）：\n" + proc.stderr.strip()[-1500:]
        )

    produced = sorted(frames_dir.glob("f*.jpg"))
    if not produced:
        raise RuntimeError("ffmpeg 沒抽到任何幀（影片可能太短或抽樣頻率過低）。")

    # showinfo 的 pts_time 依序對應每張輸出幀
    pts = [float(m) for m in _PTS_RE.findall(proc.stderr)]

    results: list[tuple[float, Path]] = []
    for i, frame in enumerate(produced):
        # showinfo 解析得到就用精準 pts_time，否則退回「起點 + n/fps」估算
        abs_t = start + pts[i] if i < len(pts) else start + (i / fps if fps > 0 else 0.0)
        newname = frames_dir / f"{i + 1:04d}_{_fmt_ts(abs_t)}.jpg"
        frame.rename(newname)
        results.append((abs_t, newname))
    return results


# ── 字幕 / 逐字稿 ─────────────────────────────────────────────────────────


def _vtt_to_text(vtt_path: Path) -> str:
    """把 WebVTT 字幕轉成 [MM:SS] 前綴的純文字，去標籤、去連續重複行。"""
    ts_re = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[.,]\d{3}\s*-->")
    tag_re = re.compile(r"<[^>]+>")
    lines: list[str] = []
    last_text = ""
    cur_start: float | None = None

    for raw in vtt_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.upper().startswith(("WEBVTT", "KIND:", "LANGUAGE:", "NOTE")):
            continue
        m = ts_re.match(line)
        if m:
            h, mm, ss = (int(x) for x in m.groups())
            cur_start = h * 3600 + mm * 60 + ss
            continue
        if "-->" in line:  # 沒被上面 regex 抓到的計時行，跳過
            continue
        text = tag_re.sub("", line).strip()
        text = re.sub(r"\s+", " ", text)
        if not text or text == last_text:
            continue
        last_text = text
        prefix = f"[{_fmt_label(cur_start)}] " if cur_start is not None else ""
        lines.append(f"{prefix}{text}")

    return "\n".join(lines).strip()


def _find_subtitle(dl_dir: Path, stem: str) -> Path | None:
    """在下載資料夾找 yt-dlp 寫出的 .vtt 字幕，偏好語言序在前的。"""
    vtts = sorted(dl_dir.glob(f"{stem}*.vtt"))
    if not vtts:
        vtts = sorted(dl_dir.glob("*.vtt"))
    if not vtts:
        return None
    for lang in DEFAULT_SUB_LANGS.split(","):
        for v in vtts:
            if f".{lang}." in v.name:
                return v
    return vtts[0]


def _transcribe_local(ffmpeg: str, video: Path, out_dir: Path, model_size: str) -> str:
    """本機檔用 faster-whisper 跑 ASR（選配、較重）。缺套件就給清楚的重跑指令。"""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "本機檔逐字稿需要 faster-whisper（沒裝進 PEP 723 預設依賴，因為它較重）。\n"
            "請改用：uv run --with faster-whisper scripts/watch_video.py <檔> --transcribe\n"
            "或：make watch-video FILE=<檔> TRANSCRIBE=1"
        ) from exc

    wav = out_dir / "audio.wav"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 抽音軌失敗：\n" + proc.stderr.strip()[-1000:])

    _eprint(f"    跑 Whisper（model={model_size}，第一次會下載模型）…")
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _info = model.transcribe(str(wav), vad_filter=True)
    lines = [f"[{_fmt_label(seg.start)}] {seg.text.strip()}" for seg in segments]
    wav.unlink(missing_ok=True)
    return "\n".join(lines).strip()


def _download_youtube(
    url: str, out_dir: Path, want_subs: bool, sub_langs: str
) -> tuple[Path, str, str | None]:
    """用 yt-dlp 下載 YouTube 影片（≤720p 單檔優先，省 ffmpeg 合流）+ 選配字幕。

    回傳 (video_path, title, subtitle_vtt_path_or_None)。
    """
    try:
        import yt_dlp  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - PEP 723 應已提供
        raise RuntimeError(
            "yt-dlp 未安裝。請用 `uv run scripts/watch_video.py ...`（PEP 723 會自動取得），"
            "不要 pip install 進 .venv。"
        ) from exc

    dl_dir = out_dir / "download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": str(dl_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "restrictfilenames": True,
    }
    if want_subs:
        opts.update(
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=sub_langs.split(","),
            subtitlesformat="vtt",
        )

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    vid_id = info.get("id", "video")
    title = info.get("title", vid_id)
    videos = [
        p for p in dl_dir.iterdir() if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    ]
    if not videos:
        raise RuntimeError("yt-dlp 下載後找不到影片檔（可能被下架或需登入）。")
    video_path = max(videos, key=lambda p: p.stat().st_size)
    subtitle = _find_subtitle(dl_dir, vid_id) if want_subs else None
    return video_path, title, subtitle


# ── 主流程 ────────────────────────────────────────────────────────────────


def _resolve_out_dir(args, source_stem: str) -> Path:
    if args.out_dir:
        return Path(args.out_dir).expanduser()
    if _looks_like_url(args.source):
        return Path.cwd() / f"{source_stem}.watch"
    src = Path(args.source).expanduser()
    return src.with_name(f"{src.stem}.watch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把影片拆成幀圖 + 逐字稿，給 Claude 分析。",
    )
    parser.add_argument("source", help="本機影片路徑，或 YouTube 連結")
    parser.add_argument("--out-dir", help="產出資料夾（預設 <影片>.watch/）")
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help=f"目標抽幀數（預設封頂 {DEFAULT_TARGET_FRAMES}）；與 --fps 二選一",
    )
    parser.add_argument(
        "--fps", type=float, default=None, help="固定每秒抽幾幀（聚焦細節用）；與 --frames 二選一"
    )
    parser.add_argument("--start", type=float, default=0.0, help="起始秒數（聚焦某段）")
    parser.add_argument("--end", type=float, default=None, help="結束秒數（聚焦某段）")
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH, help=f"幀圖寬度 px（預設 {DEFAULT_WIDTH}）"
    )
    parser.add_argument("--no-subs", action="store_true", help="YouTube 不抓字幕（預設會抓）")
    parser.add_argument("--sub-langs", default=DEFAULT_SUB_LANGS, help="字幕語言偏好序（逗號分隔）")
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="本機檔跑 Whisper 逐字稿（較重、需 `uv run --with faster-whisper`）",
    )
    parser.add_argument(
        "--whisper-model", default="base", help="Whisper 模型大小（tiny/base/small/...，預設 base）"
    )
    args = parser.parse_args(argv)

    try:
        ffmpeg, ffprobe = _ffmpeg_paths()
    except RuntimeError as exc:
        _eprint(f"error: {exc}")
        return 1

    is_url = _looks_like_url(args.source)
    if not is_url:
        src_path = Path(args.source).expanduser()
        if not src_path.is_file():
            _eprint(f"error: 找不到影片檔 {src_path}")
            return 1
        source_stem = src_path.stem
    else:
        source_stem = "youtube"

    out_dir = _resolve_out_dir(args, source_stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    # 清掉舊幀，避免混到上一次的結果
    if frames_dir.exists():
        for old in frames_dir.glob("*.jpg"):
            old.unlink()

    title = source_stem
    transcript_text = ""
    transcript_source = None

    # 1) 取得影片檔（YouTube 先下載 + 抓字幕）
    try:
        if is_url:
            _eprint(f"[1/4] 下載 YouTube：{args.source}")
            video, title, sub_vtt = _download_youtube(
                args.source, out_dir, want_subs=not args.no_subs, sub_langs=args.sub_langs
            )
            if sub_vtt is not None:
                transcript_text = _vtt_to_text(sub_vtt)
                transcript_source = f"youtube-subs:{sub_vtt.name}"
                _eprint(f"      抓到字幕：{sub_vtt.name}（{len(transcript_text)} 字）")
            else:
                _eprint("      這支沒有可用字幕。")
        else:
            video = src_path
            _eprint(f"[1/4] 本機檔：{video}")
    except RuntimeError as exc:
        _eprint(f"error: {exc}")
        return 1

    # 2) 讀時長、算抽樣頻率
    try:
        duration = _probe_duration(ffprobe, video)
        start = max(0.0, args.start)
        end = args.end if args.end is not None else duration
        end = min(end, duration)
        if end <= start:
            _eprint(f"error: --end ({end}) 必須大於 --start ({start})")
            return 1
        window = end - start
        fps, est = _compute_fps(window, args.fps, args.frames)
        _eprint(
            f"[2/4] 時長 {_fmt_label(duration)}；抽樣區間 {_fmt_label(start)}–{_fmt_label(end)}"
            f"（{_fmt_label(window)}），fps≈{fps:.3f}，預估 ~{est} 幀，寬 {args.width}px"
        )
    except (RuntimeError, ValueError) as exc:
        _eprint(f"error: {exc}")
        return 1

    # 3) 抽幀
    try:
        _eprint("[3/4] 抽幀中…")
        frames = _extract_frames(ffmpeg, video, frames_dir, start=start, fps=fps, width=args.width)
        _eprint(f"      抽出 {len(frames)} 幀 → {frames_dir}")
    except RuntimeError as exc:
        _eprint(f"error: {exc}")
        return 1

    # 4) 本機檔逐字稿（選配）
    if args.transcribe and not is_url:
        try:
            _eprint("[4/4] 本機檔逐字稿（Whisper）…")
            transcript_text = _transcribe_local(ffmpeg, video, out_dir, args.whisper_model)
            transcript_source = f"whisper:{args.whisper_model}"
            _eprint(f"      逐字稿完成（{len(transcript_text)} 字）")
        except RuntimeError as exc:
            # 逐字稿失敗不擋整體；幀還在，明講原因
            _eprint(f"warn: 逐字稿略過 — {exc}")
    elif not is_url and not args.transcribe:
        _eprint("[4/4] 本機檔逐字稿：略過（要的話加 --transcribe）")

    # 寫 transcript.txt（沒抓到文字就連來源標記一起清掉，讓 manifest 誠實）
    transcript_path = None
    if transcript_text:
        transcript_path = out_dir / "transcript.txt"
        transcript_path.write_text(transcript_text, encoding="utf-8")
    else:
        transcript_source = None

    # 寫 manifest.json（機器可讀，給 /watch-video 指令對照）
    manifest = {
        "source": args.source,
        "is_url": is_url,
        "title": title,
        "duration_seconds": round(duration, 3),
        "window": {"start": round(start, 3), "end": round(end, 3)},
        "sample_fps": round(fps, 6),
        "frame_width": args.width,
        "frame_count": len(frames),
        "frames": [
            {
                "index": i + 1,
                "t_seconds": round(t, 3),
                "t_label": _fmt_label(t),
                "path": str(p.relative_to(out_dir)),
            }
            for i, (t, p) in enumerate(frames)
        ],
        "transcript_path": (str(transcript_path.relative_to(out_dir)) if transcript_path else None),
        "transcript_source": transcript_source,
        "notes": (
            "時間戳為抽樣點的近似值。抽出的任何數字/價格僅供分析參考，"
            "要入帳仍走 restaurant_api 結構化驗證。"
        ),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 乾淨摘要 → stdout（給 make / Claude 擷取）
    print("── watch-video 產出 ──────────────────────────────")
    print(f"標題    : {title}")
    print(f"時長    : {_fmt_label(duration)}")
    print(f"抽幀    : {len(frames)} 張（{_fmt_label(start)}–{_fmt_label(end)}，寬 {args.width}px）")
    print(f"逐字稿  : {'有（' + str(transcript_source) + '）' if transcript_text else '無'}")
    print(f"產出夾  : {out_dir}")
    print(f"幀資料夾: {frames_dir}")
    print(f"索引    : {manifest_path}")
    print("下一步  : Claude 先 Read manifest.json，再逐張 Read frames/，對照 transcript.txt 回答。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
