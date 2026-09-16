# 20 — Watch-Video（影片拆解）設定與使用

> 什麼時候用：想讓 Claude **看一支影片並回答問題**時——拆對手品牌片、餐飲宣傳片、
> YouTube 行銷案例的敘事結構 / 鏡頭節奏 / 賣點編排，或問「這一幕螢幕上寫什麼」。
> 它把影片抽成**時間戳幀圖 + 逐字稿**，Claude 再逐幀看、對照旁白回答。

---

## 這是什麼

- **指令**：`/watch-video <影片路徑或 YouTube 連結> "你的問題"`（`.claude/commands/watch-video.md`，已進版控）。
- **引擎**：`scripts/watch_video.py`——uv-isolated PEP 723 腳本，跟 `to_md.py` 同一套路
  （不進 `pyproject.toml`、碰不到 `.venv`）。
- **原理**：`ffmpeg` 低頻抽樣出幀圖（預設最多 100 張）＋逐字稿 → Claude 的原生 `Read`
  逐張看圖 + 讀逐字稿 → 回答。Claude 讀不了影片，但讀得了圖，這支就是「先轉一手」。

用法最短路徑：

```
/watch-video ~/Downloads/demo.mp4 "這支片的敘事結構跟賣點編排是什麼"
/watch-video https://youtu.be/xxxx "鏡頭節奏怎麼安排的"
```

或直接在對話裡丟路徑／連結，Claude 會自動接手。

---

## 一次性前置：裝 ffmpeg

抽幀與讀時長都靠系統的 `ffmpeg` / `ffprobe`（不是 Python 套件，所以不走 uv）：

```bash
# macOS
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg
```

裝完驗證：`ffmpeg -version`。**沒裝的話腳本會直接告訴你怎麼裝**，不會神祕失敗。

`yt-dlp`（YouTube 下載）**不用手動裝**——PEP 723 已宣告，`uv run` 第一次跑會自動取得並快取。

> **在哪裡跑**：這工具是給**本機 Claude Code**用的（你的 Mac 上有影片、有 ffmpeg）。
> Claude Code on the web 的雲端沙盒是臨時的、也沒有你的影片檔，不是這工具的主場。
> 若要在雲端拆 YouTube，得先把 `*.youtube.com`、`*.googlevideo.com` 等網域開白名單（見 `docs/18`）。

---

## 三個邊界（先知道，省得誤會）

1. **來源**：本機影片檔（mp4/mov/mkv/webm…）✅、YouTube 連結 ✅。
   IG Reels / 抖音 / 小紅書 / TikTok 這類**不支援**——先自己下載成檔案再丟。
2. **精細度**：預設**抽樣式理解**（最多 100 幀，約每秒 1 張封頂——短片自然抽得少、
   長片壓到 100 張上限），不是逐格。短片看得細；一小時長片是「低頻抽樣＋逐字稿」的摘要。
   要看某一幕細節就**聚焦**（下方 drill-down）。
3. **成本**：抽越多幀，token 燒越凶。**長片先看結構、再鑽細節**。

---

## 怎麼用（日常）

用白話交代即可，Claude 會自動觸發指令。實際會發生的事：

1. Claude 依影片長度決定抽幾幀（長片先少抽），跑 `scripts/watch_video.py`。
2. 產出寫到影片旁的 `<影片>.watch/`：`frames/`（時間戳幀圖）、`transcript.txt`、`manifest.json`。
3. Claude Read 這些素材，回答你的問題，提到片段時**標時間戳**讓你能回去對照。

### 聚焦某一幕（看清楚細節）

```
「1:30 那一幕螢幕上寫什麼」
```

Claude 會只重抽那一小段、拉高解析度：

```bash
uv run scripts/watch_video.py <來源> --start 90 --end 96 --fps 2 --width 1280
```

### 手動直接跑（不透過 Claude）

```bash
make watch-video FILE=~/Downloads/demo.mp4              # 抽幀（+YouTube 字幕）
make watch-video FILE=~/Downloads/demo.mp4 TRANSCRIBE=1 # 本機檔 + Whisper 逐字稿
uv run scripts/watch_video.py demo.mp4 --frames 24      # 先抽 24 幀看結構
uv run scripts/watch_video.py demo.mp4 --help           # 所有旗標
```

### 逐字稿分層
- **YouTube**：自動抓字幕（便宜、免 ASR）。
- **本機檔**：要 ASR，加 `--transcribe`（走 `uv run --with faster-whisper …`，第一次下載模型較久）。
  沒特別要求就不轉。

---

## 常用旗標

| 旗標 | 作用 | 預設 |
|---|---|---|
| `--frames N` | 目標抽幀數（封頂用） | 100 |
| `--fps F` | 固定每秒抽幾幀（聚焦用，與 `--frames` 二選一） | — |
| `--start` / `--end` | 只抽某段（秒） | 全片 |
| `--width` | 幀圖寬度 px | 640 |
| `--transcribe` | 本機檔跑 Whisper 逐字稿 | 關 |
| `--no-subs` | YouTube 不抓字幕 | 關（預設會抓） |
| `--out-dir` | 自訂產出夾 | `<影片>.watch/` |

硬上限 400 幀：長片 × 高 fps 會直接擋下，請縮範圍或調低 fps，避免 token 燒爆。

---

## 鐵律（呼應 CLAUDE.md）

- **抽出的數字只是參考**：對手菜單價目板、促銷數字等，要入帳仍走 `restaurant_api`
  結構化驗證 + 人工覆核，別直接信幀圖/逐字稿的純文字。
- **只拆單支、你指定的影片**，不做無腦全頻道/全站爬（封號 + ToS 風險）。
- 產出夾 `*.watch/` 是本地暫存（已 gitignore），**不 commit**。

---

## 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `缺少系統工具 ffmpeg` | 沒裝：`brew install ffmpeg`（macOS）/ `sudo apt install ffmpeg` |
| `讀不到影片時長` | 檔案損壞或不是影片；換檔或確認路徑 |
| YouTube `找不到影片檔` | 影片下架 / 需登入 / 地區限制；換一支或先自行下載 |
| 這支「沒有可用字幕」 | 該片沒上字幕；本機下載後用 `--transcribe` 跑 Whisper |
| 逐字稿要 faster-whisper | 用 `uv run --with faster-whisper scripts/watch_video.py <檔> --transcribe` |
| 幀太多、跑很久/很貴 | 先 `--frames 24` 看結構，再 `--start/--end` 聚焦；或撞到 400 硬上限就縮範圍 |
| 想在雲端 session 拆 YouTube | 先開網路白名單（`*.youtube.com`、`*.googlevideo.com`…），見 `docs/18` |
