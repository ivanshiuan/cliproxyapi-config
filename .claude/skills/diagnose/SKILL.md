---
name: diagnose
description: "系統化除錯（假設→證據→根因）。當遇到不明錯誤、測試紅了原因不明、行為跟預期不符、或使用者說『幫我診斷』『查一下為什麼』時使用。禁止亂槍打鳥式改程式碼；先重現、再定位、找到根因才動刀。"
allowed-tools: Read, Bash, Glob, Grep
---

# diagnose — 假設 → 證據 → 根因

程式碼愈修愈壞的根因是**沒診斷就動刀**。這個 skill 強制先有證據再有修改。

## 流程

1. **重現**：找到最小重現步驟。不能穩定重現的 bug 不要修 — 先想辦法讓它穩定出現。
   - 測試紅：`.venv/bin/pytest <失敗的那一個> -x -l --tb=long`
   - API 異常：看結構化日誌（JSON，有 request context），curl 打 `/health/ready` 確認基礎設施。
2. **列假設**：寫下 2-4 個可能原因，**按可能性排序**。
3. **驗證**：對最可能的假設找證據 — 讀 code、加暫時 log、隔離變因。一次驗一個。
   - 證據推翻假設 → 劃掉，換下一個。不要「順便改改看」。
4. **根因**：能用一句話講出「為什麼會發生」才算找到。「改了這行就好了」不算。
5. **修**：修根因，不是修症狀。加一個會抓到這隻 bug 的回歸測試（接 tdd skill 的紅→綠）。
6. **清場**：移除診斷用的暫時 log / print。

## 本專案的高頻根因（先對照，常常 30 秒就破案）

| 症狀 | 高機率根因 |
|---|---|
| `Future attached to different loop` | 用了 sync TestClient → 改 conftest 的 async client |
| POST 成功但接著查 not found | service 只 flush 沒 commit → 確認走 `api/deps.py::get_db` |
| 單測過、合跑爆 | DB 殘留 → `make db-truncate`；或測試沒 scope 到 seed fixture |
| Alembic autogenerate 炸 | PG 沒起 → `sudo service postgresql start` |
| pyright 報 alembic unknown | 正常，versions 已 exclude |
| 金額差一分錢 | 有 float 混進來 → grep 整條計算路徑 |

## 鐵律

- **禁止**在還沒重現前就改程式碼。
- **禁止**一次改多個地方再看有沒有好 — 那樣好了也不知道為什麼好。
- 修完必跑 `make full-check`，並回報「根因一句話 + 修了什麼 + 回歸測試在哪」。
