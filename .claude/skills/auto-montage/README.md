# Auto-Montage

> 一個 **Claude Code / Agent Skill**：把你的 AI 編碼助手變成**無頭（headless）自動影片工作室**。
> 自然語言下一句需求 → 自動完成 研究 → 腳本 → 找素材 → 配音 → 剪輯 → **雙語字幕** → 算圖成片。

**不需要 CapCut、不需要 Computer Use、不需要桌面 GUI。** 全程 CLI，零金鑰即可起步。

---

## 這是什麼

Auto-Montage 融合兩個開源專案的長處、砍掉短處，包成一個可直接給 Claude Code 用的 Skill：

- **執行引擎**：[OpenMontage](https://github.com/calesthio/OpenMontage)（headless、多供應商、免費基線、預算治理）—— 由你自行安裝，本 Skill 透過其公開 CLI 呼叫。
- **方法論大腦**：原創的踩坑庫、留存/鉤子、雙語字幕對齊紀律、餐飲短影音模板（靈感來自 [video-autopilot-kit](https://github.com/Hao0321/video-autopilot-kit)，內容全為重寫）。

> **授權**：本 Skill 原創部分採 **MIT**。OpenMontage 為 AGPLv3，僅作外部依賴呼叫，**不**內含其程式碼，
> 因此整合進閉源產品不會沾到 copyleft。細節見 [`ATTRIBUTION.md`](./ATTRIBUTION.md)。

## 安裝

1. **裝引擎**（一次性）：
   ```bash
   git clone https://github.com/calesthio/OpenMontage.git
   cd OpenMontage && make setup
   export OPENMONTAGE_HOME="$PWD"
   ```
   需求：Python 3.10+、Node.js 18+、ffmpeg/ffprobe。

2. **裝 Skill**：把這個資料夾放到你的專案 `.claude/skills/auto-montage/`（Claude Code 會自動載入）。

3. **驗證**：
   ```bash
   bash .claude/skills/auto-montage/scripts/preflight.sh
   ```

## 用法

對 Claude Code 說，例如：

- 「做一支 30 秒直式短影音介紹我們的招牌牛肉麵，中英雙語字幕，零金鑰免費跑。」
- 「把這批菜色素材剪成 60 秒宣傳片，前 2 秒要有鉤子，BGM 不要蓋過上菜聲。」

Skill 會：preflight → 用方法論塑形 brief → 驅動 OpenMontage 逐階段生成 → 過雙語字幕對齊閘 → 報成本與核准 → 交成片。

## 內容
```
auto-montage/
├── SKILL.md                      # 主指令（Claude 讀這個）
├── knowledge/pitfalls.md         # 踩坑庫 → 每片 QA 清單
├── knowledge/retention-and-hooks.md
├── templates/restaurant-vlog.md  # 餐飲短影音模板
├── templates/brief.example.yaml  # 收稿骨架
└── scripts/
    ├── preflight.sh              # 引擎/工具偵測
    └── subtitle_align_check.py   # 雙語字幕對齊/可讀性閘
```

## 授權
MIT（見 [`LICENSE`](./LICENSE)）。上游致謝與 AGPL 邊界見 [`ATTRIBUTION.md`](./ATTRIBUTION.md)。
