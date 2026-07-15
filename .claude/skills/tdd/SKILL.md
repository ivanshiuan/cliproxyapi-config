---
name: tdd
description: "測試先行的回饋循環（紅→綠→重構）。當要新增業務邏輯、修 bug、或使用者說『用 TDD 做』『先寫測試』時使用。強制先寫會失敗的測試、看它紅、再寫最小實作讓它綠，最後重構。搭配本專案的 conftest fixture 與 SAVEPOINT 機制。"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# tdd — 紅 → 綠 → 重構

程式碼不能正常運作的根因是**缺乏回饋循環**。這個 skill 強制回饋循環存在。

## 循環（一次一個行為，不要貪）

1. **紅**：先寫一個描述期望行為的測試，跑它，**親眼看它失敗**。
   - 沒看到紅就寫實作 = 你不知道測試到底有沒有在測東西。
   - 失敗訊息要是「預期的那種失敗」（assert 失敗），不是 import error 或 fixture 炸掉。
2. **綠**：寫**最小**實作讓這個測試過。抗拒順手多寫的衝動。
3. **重構**：測試綠著的狀態下整理程式碼。跑一次確認還是綠。
4. 回到 1，做下一個行為。

## 本專案的具體規矩（不遵守會踩坑）

- Router 整合測用 `tests/conftest.py` 的 `client` fixture（`httpx.AsyncClient` + `ASGITransport`）。
  **絕對不要** sync `TestClient` — 會 event loop 衝突。
- 查詢 scope 到 `seed_tenant` / `seed_store` fixture，不要全表掃描（會撞 seed/demo 資料）。
- 每測自帶 SAVEPOINT、自動 rollback；測試之間不共享狀態，也不要依賴執行順序。
- 錢用 `Decimal`，測試裡的期望值也用 `Decimal("12.3400")`，不要 float 比較。
- 跑單測：`.venv/bin/pytest tests/<path> -x -q`。合跑爆但單跑過 → `make db-truncate`。

## 完成定義

- 新行為的每條 AC 各有至少一個測試對應。
- `make full-check` 全綠（ruff + pyright + pytest + alembic + smoke）。
- 測試名講人話：`test_order_discount_rejects_expired_voucher`，不是 `test_case_3`。

## 反模式（看到就停）

- 實作寫完才補測試「讓覆蓋率好看」— 那不是 TDD，回到步驟 1。
- 一次寫 10 個測試再開始實作 — 一次一個。
- 測試紅了就改測試遷就實作 — 先確認到底是誰錯。
