# 20 — Obsidian 第二大腦：本機安裝教學

> 承接 `docs/19_empire_brain.md` 路線圖第 3 步（「匯出到 Obsidian vault → 格局圖」）。
> 這份文件只講**怎麼把 `claude-obsidian` 裝到你自己的電腦上**，不涉及本 repo 的程式改動。

---

## 先講清楚：這件事不能在雲端 session 裡做

Claude Code on the web／這個雲端沙盒容器，跟你的 Mac/PC 是**兩台不同機器**——沒有連到你的實體電腦，也看不到你桌面上的 Obsidian。

`claude-obsidian` 需要：
1. 一個裝了 **Obsidian 桌面版**的真實電腦（有 GUI）
2. 該電腦上的 **Claude Code CLI**（`claude` 指令）

兩者都得在**你自己的電腦**跑，所以以下步驟要在你的 Terminal 上手動執行。

---

## 前置確認

```bash
claude --version   # 沒有就去 claude.com/code 裝
git --version
```

Obsidian 桌面版：<https://obsidian.md> 下載安裝。

---

## 安裝步驟

### 1. 選一個跟正式業務隔離的資料夾

不要放在這個 repo（`cliproxyapi-config`）或其他正式專案目錄下面，另開一個乾淨位置：

```bash
cd ~/Desktop
mkdir ai-secondbrain
cd ai-secondbrain
git clone https://github.com/AgriciDaniel/claude-obsidian
cd claude-obsidian
bash bin/setup-vault.sh
```

### 2. 在 Obsidian 開這個資料夾

Obsidian → **Manage Vaults** → **Open folder as vault** → 選 `claude-obsidian/`。

### 3. 同一個資料夾裡開 Claude Code

確認人在 `ai-secondbrain/claude-obsidian` 目錄下：

```bash
claude
```

進去後執行：

```
/wiki
```

它會問「這個 vault 是做什麼用的？」——**把用途講清楚**，例如：
「個人化的產業研究與靈感收集，不涉及公司核心財務、交易策略、客戶資料」。
講清楚用途，AI 才不會把敏感內容也一起吸進去。

### 4. 第一次先別開網路存取

第一次設定完先**不要**加 `--allow-egress`，`/autoresearch` 保持預設關閉。
等確認資料流向沒問題、且真的需要它去抓外部資料時，再考慮開。

---

## 建議的試用方式

先丟幾份**非核心資料**（產業文章、競品研究）進去跑兩週，看它整理出來的圖譜有沒有實際幫到判斷，
再決定要不要投入時間餵核心業務資料——不要一開始就把帝國營運的核心資料（財務、會員、供應鏈）餵進去。

裝好、跑起來之後，如果想檢查設定檔或 `CLAUDE.md`／vault 內容對不對，可以把內容貼回這個 repo 的 session 給我看，我可以幫忙審。

---

## 跟本專案知識庫的關係

本 repo 的 `docs/knowledge/`（`00_MOC.md` + `digest` skill 產出的知識卡）已經是一個能直接跑的
markdown 知識庫，Obsidian 只是**多一層視覺化 / 本機 vault** 的選項，兩者不衝突：

- 量還小、單純想要 AI 讀寫 + 版控 → 留在 `docs/knowledge/` 就夠（見 `docs/19_empire_brain.md`）
- 想要 Canvas 格局圖、Graph view、本機獨立管理 → 照本文把 `docs/knowledge/` 的知識卡複製/同步進 `claude-obsidian` vault
