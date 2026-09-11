---
description: 拆解一支影片（本機檔或 YouTube）— 抽時間戳幀圖 + 逐字稿，回答你的問題
argument-hint: <影片路徑或 YouTube 連結> ["你的問題"] [--focus MM:SS] [--frames N]
allowed-tools: Bash(uv run scripts/watch_video.py:*), Bash(make watch-video:*), Read
---

你要幫 Ivan 拆解一支影片並回答他的問題。`$ARGUMENTS` 裡有：**來源**（本機路徑或
YouTube 連結）＋通常一句**問題**（引號內）。若沒問題，預設先給「整體結構速覽」。

底層工具是 `scripts/watch_video.py`（uv-isolated，見 `docs/20_watch_video_setup.md`）。
它把影片**低頻抽樣**成一疊時間戳幀圖 + 逐字稿；你負責 **Read 幀 + 對照逐字稿 + 回答**。

## 標準流程

1. **抽素材**。先判斷影片長度決定抽幾幀（見下方「成本」）。跑：

   ```bash
   uv run scripts/watch_video.py "<來源>" [--frames N]
   ```

   - 缺 `ffmpeg` 會直接報「怎麼裝」——把訊息轉給 Ivan，不要自己瞎試。
   - YouTube 會自動抓字幕；本機檔預設不轉逐字稿（要的話見下方 `--transcribe`）。

2. **讀索引**。`Read` 產出夾裡的 `manifest.json` — 拿到時長、每幀的
   `t_label`（時間戳）與 `path`、以及 `transcript_path`。

3. **看畫面**。逐張 `Read` `frames/` 底下的幀圖（檔名已含時間戳，如
   `0007_00-01-30.jpg` = 約 1:30）。有 `transcript.txt` 就一起 Read，**畫面配旁白**一起理解。

4. **回答**。針對 Ivan 的問題作答；提到具體片段時**標時間戳**（例：「1:30 的價目板寫…」），
   讓他能自己回去對照。

## 三個邊界（照 Ivan 的認知，主動講清楚）

1. **來源**：本機影片檔（mp4/mov…）✅、YouTube ✅。IG Reels / 抖音 / 小紅書 / TikTok 這類
   連結**不在支援範圍**——請 Ivan 先自己下載成檔案再丟。（要抓社群公開頁的「文字」可用 `digest`；
   要「登入操作」用 `browser-act`。）
2. **精細度**：預設是**抽樣式理解**（最多 100 幀），不是逐格。短片看得細，一小時的長片是
   「低頻抽樣＋逐字稿」的摘要。要看某一幕細節就**聚焦**（見下）。
3. **成本**：抽越多幀，token 燒越凶。**長片（>15 分）先抽少量看結構**（`--frames 24`），
   拿到 Ivan 想鑽的段落再高解析聚焦。動手抽一大批幀前，若會很貴，先跟 Ivan 講一聲。

## 聚焦某一幕（drill-down）

Ivan 問「1:30 那一幕螢幕上寫什麼」→ 只重抽那一小段、拉高解析：

```bash
# 1:30 ≈ 90 秒，看 90–96 秒，每秒 2 幀、寬 1280
uv run scripts/watch_video.py "<來源>" --start 90 --end 96 --fps 2 --width 1280
```

再 `Read` 新抽出的高解析幀回答。**聚焦時範圍要小**（幾秒～幾十秒），才不會又抽爆。

## 逐字稿

- **YouTube**：自動抓字幕，不用加旗標。
- **本機檔**（要 ASR）：較重、選配。跑
  `uv run --with faster-whisper scripts/watch_video.py "<檔>" --transcribe`
  （第一次會下載 Whisper 模型）。沒特別要求就不轉，省時間。

## 鐵律

- **抽出的數字只是參考**：對手菜單價目板、促銷數字等，要入帳仍走 `restaurant_api`
  結構化驗證 + 人工覆核，別直接信幀圖/逐字稿吐的純文字（呼應 CLAUDE.md 金錢法則）。
- **只做單支、Ivan 指定的影片**，不做無腦全頻道/全站爬（封號 + ToS 風險）。
- 產出夾 `<影片>.watch/` 是本地分析暫存（已 gitignore），答完可留著讓 Ivan 複看，不要 commit。

## 對 Ivan 實際的用途（主動連結場景）

拆對手品牌片 / 餐飲宣傳片 / YouTube 行銷案例的**敘事結構、鏡頭節奏、賣點編排**——
這是 BUFF HOTPOT 內容產線的分析利器。答完可順手問 Ivan 要不要用 `digest` 存成知識卡。
