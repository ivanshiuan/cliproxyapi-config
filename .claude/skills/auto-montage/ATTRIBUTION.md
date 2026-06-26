# Attribution & Licensing｜致謝與授權邊界

This Skill is an **original, clean-room** work. It does **not** copy, vendor, or
redistribute source code from the upstream projects below. It interoperates with
one of them (OpenMontage) by calling its **public command-line interface** — this
is interoperation/aggregation, not a derivative work.

本 Skill 為**原創、clean-room** 作品。它**不複製、不內含、不轉散布**下列上游專案的程式碼。
它只透過 **OpenMontage 的公開 CLI 合約**呼叫之（屬互通/聚合，非衍生作品）。

## 本 Skill 的授權

- 本 Skill 自身的所有檔案（`SKILL.md`、`knowledge/`、`templates/`、`scripts/`）
  皆為原創，採 **MIT** 授權（見 `LICENSE`）。
- 因此把本 Skill 整合進閉源/商用產品**不會**觸發 copyleft。

## 上游致謝（靈感與互通對象，非程式碼來源）

### OpenMontage — https://github.com/calesthio/OpenMontage
- 授權：**GNU AGPLv3**。
- 角色：**外部執行引擎**，由使用者自行 `git clone` 安裝；本 Skill 透過其公開 CLI 呼叫。
- ⚠️ **AGPL 提醒**：若你**修改 OpenMontage 本體**並以**網路服務**形式對外提供，AGPLv3 第 13 條
  會要求你公開該修改版原始碼。**只是透過 CLI 呼叫未修改的 OpenMontage 來產出影片，不觸發此義務**
  （影片產出物不受 copyleft 約束）。**不要**把 OpenMontage 的程式碼複製進本 Skill 或你的閉源產品。

### video-autopilot-kit — https://github.com/Hao0321/video-autopilot-kit
- 授權：**MIT**。
- 角色：**方法論靈感來源**。本 Skill 的踩坑庫、雙語字幕紀律、餐飲模板、留存鉤子等內容皆為
  **重新撰寫的原創**，未複製其文字或程式碼。其 CapCut GUI／Computer Use 依賴已被本 Skill 刻意捨棄。

## 維護紅線

- 不要把 OpenMontage 或 video-autopilot-kit 的任何檔案 `cp` 進本資料夾。
- 需要新功能時，**呼叫**引擎 CLI 或**自己原創**，不要貼上游程式碼。
- 散布本 Skill 時保留本檔與 `LICENSE`。
