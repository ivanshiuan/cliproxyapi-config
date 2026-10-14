# PR 與分支台帳（快照）

> 資料時點：**2026-10-09**（唯讀盤點）；2026-10-14 複核：#29 / #31 / #45 / #40 仍為 open、預設分支頭仍是 `759c121`（9/16）。
> 這是一次性快照，不是自動更新的狀態；PR 狀態以 GitHub 為準。預設分支 `claude/autonomous-resttech-enterprise-oW9jp`（**沒有 `main`**）。
> 31 個開放 PR · 59 條遠端分支 · 16 個已關閉 PR。**沒有任何一個 PR 有人類 approve**（只有 CodeRabbit 的 COMMENTED）。

## 一頁結論

1. **能在交付前進主幹的只有兩個**：#31（LINE 分眾推播 DI 修復 + 正式環境 compose 加固，7 檔 +610/-24）與 #29（POS 全套，114 檔 +18,680/-43）。兩者對預設分支都無衝突、CI 綠，`#31 → #29` 與 `#29 → #31` 兩種順序都無衝突。
2. **Alembic 多頭是最大的合併陷阱**：預設分支單一 head `41772db92af3`，但 **5 條獨立 PR 鏈 + 2 條無 PR 分支鏈全都把 `down_revision` 指向它**。合第一個之後，其餘都要改接新 head，否則 `alembic upgrade head` 直接失敗。
3. **`tests/test_restaurant_api.py` 寫死表數**（預設為 33）：每個加表的 PR 都改同幾行 → 文字衝突，且合併後數字要重算。
4. **CI 從沒在「合併結果」上跑過**：原本 `pull_request` 只列 `main`（不存在）。PR #40 的 `ci.yml` 已修正。
5. **與餐飲無關的 PR（賽事下注、車牌平台、影片工具、補助雷達…）建議整批 PARK**，不要與交付路徑混在一起。

## 建議合併順序

| # | PR | 分級 | 類別 | 交付相關 | 理由 |
|---:|---:|---|---|---|---|
| 1 | #31 | T1_SHIP_1013 | RESTAURANT_CORE | MUST | smallest safe fix: LINE broadcast DI bug + prod compose hardening; clean+green; no migration |
| 2 | #29 | T1_SHIP_1013 | RESTAURANT_CORE | MUST | THE deliverable (POS); clean+green; must land before any other migration PR (head b7d2e9a41f05) |
| 3 | #45 | T2_CONDITIONAL | FINANCE_BACK_OFFICE | CONDITIONAL | only if Odoo is in 10/13 scope; re-parent 8307a94acb62 onto b7d2e9a41f05; fix table-count test (44) |
| 4 | #11 | T3_AFTER_DELIVERY | KNOWLEDGE_ONLY | DEFER | docs base of the auth stack (#12 base branch) |
| 5 | #12 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | auth PR-A; migration a4b7c1d2e3f9 re-parent; CI red; conflicts pyproject.toml |
| 6 | #14 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | auth PR-B; hard conflict with #29 (AuthError vs AuthenticationError, deps.py); CI red |
| 7 | #20 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | auth PR-C; flips auth_enforcement->enforce (breaks POS UIs); CI red |
| 8 | #21 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | auth PR-D test fixtures; needs #20; CI red |
| 9 | #22 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | auth PR-E bootstrap CLI/docs; last of stack; CI red |
| 10 | #17 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | AI marketing; GH-dirty; conflicts with #29/#45/#26; migration c3d4e5f6a1b2 re-parent; uv.lock |
| 11 | #26 | T3_AFTER_DELIVERY | RESTAURANT_CORE | DEFER | BUFF OS founder tooling; empty body; 4 tables; re-parent 8b3f2a7c9e14; CI red |
| 12 | #19 | T4_DOCS | KNOWLEDGE_ONLY | DOCS | single playbook doc; clean |
| 13 | #9 | T4_DOCS | KNOWLEDGE_ONLY | DOCS | PRD/specs; GH-dirty on pyproject.toml |
| 14 | #23 | T4_DOCS | RESTAURANT_CORE | DOCS | after #31 only empire/*.md remains; 含商業敏感內容，合併前請創辦人審閱 |
| 15 | #40 | T4_DOCS | KNOWLEDGE_ONLY | DOCS | knowledge card; MOC conflict with #34/#43 |
| 16 | #34 | T4_DOCS | KNOWLEDGE_ONLY | DOCS | knowledge card; MOC conflict with #40/#43 |
| 17 | #37 | T4_DOCS | KNOWLEDGE_ONLY | DOCS | Obsidian guide; clean |
| 18 | #24 | T5_OPS | OPS_TOOLING | DEFER | CLAUDE.md +6; defer to avoid churn |
| 19 | #35 | T5_OPS | OPS_TOOLING | DEFER | .codegraph/.gitignore; CI red = ruff lint on stale base (jobs API) |
| 20 | #30 | T5_OPS | OPS_TOOLING | DEFER | LiteLLM routing config; clean+green |
| 21 | #10 | T5_OPS | OPS_TOOLING | DEFER | DevSwarm spec contract; rewrites slash commands; CI red |
| 22 | #32 | T5_OPS | OPS_TOOLING | DEFER | skill install; conflicts #36 on CLAUDE.md; CI red |
| 23 | #36 | T5_OPS | OPS_TOOLING | DEFER | SessionStart hook edits .claude/settings.json; test first |
| 24 | #28 | T5_OPS | OPS_TOOLING | DEFER | skill install (16 files); CI red |
| 25 | #42 | T5_OPS | OPS_TOOLING | DEFER | WrenAI tooling; CLAUDE.md conflict with #45 |
| 26 | #43 | T5_OPS | OPS_TOOLING | DEFER | Orca scripts; duplicate DI hunk; MOC conflict |
| 27 | #8 | PARK | OFF_MISSION | PARK | Goodgle pet-app GTM doc: off-mission |
| 28 | #44 | PARK | OFF_MISSION | PARK | /watch-video tool: off-mission |
| 29 | #41 | PARK | OFF_MISSION | PARK | subsidy_radar package: off-mission |
| 30 | #25 | PARK | OFF_MISSION | PARK | PlateGo: off-mission; same idea as #33 |
| 31 | #33 | PARK | OFF_MISSION | PARK | car16 plate platform: off-mission; same idea as #25 |

分級：`T1_SHIP_1013` 交付前合併 · `T2_CONDITIONAL` 僅當 Odoo 在範圍內 · `T3_AFTER_DELIVERY` 需 migration 改接/衝突處理 · `T4_DOCS` 純文件 · `T5_OPS` 開發工具、交付凍結後再說 · `PARK` 離題。

## Alembic 多頭

共同的 `down_revision = 41772db92af3`（預設分支現行 head）。7 條互相獨立的鏈：

| 起始 revision | 結尾 head | 攜帶者 | 新增表數 |
|---|---|---|---:|
| `a4b7c1d2e3f9` | `b5c8d3e1f0aa` | #12, #14, #20, #21, #22 | 6 |
| `c3d4e5f6a1b2` | `c3d4e5f6a1b2` | #17 | 3 |
| `20b062b46d2c` | `b7d2e9a41f05` | #29 | 8 |
| `b7e4a1c93d20` | `b7e4a1c93d20` | claude/launch-wheel-game-campaign-t7octp | 2 |
| `8b3f2a7c9e14` | `6d4a1e2b8f37` | #26 | 4 |
| `8307a94acb62` | `938b0ab19f78` | #45, recovery/odoo-integration-clean-pr38 | 3 |
| `a7c31f08d442` | `a7c31f08d442` | claude/restaurant-system-competitor-analysis-ei6tos | 4 |

**改接方式**（每合一個，就把下一個的 `down_revision` 改成當時的 head；或用 `alembic merge`）：

- after #29 merges: head = b7d2e9a41f05
- #45 (if in scope): set 8307a94acb62.down_revision = b7d2e9a41f05 -> head 938b0ab19f78
- auth stack #12: set a4b7c1d2e3f9.down_revision = <previous head> -> head b5c8d3e1f0aa
- #17: set c3d4e5f6a1b2.down_revision = <previous head>
- #26: set 8b3f2a7c9e14.down_revision = <previous head> -> head 6d4a1e2b8f37
- alternative: `alembic merge -m ... <heads>` revision; either way re-run CI steps `alembic upgrade head`, `alembic check`, scripts/check_migration_safety.py

每次改接後都要重跑：`alembic upgrade head` → `alembic check` → `python scripts/check_migration_safety.py`。

### 其他合併風險

- **table-name collision (not a head problem)**：claude/restaurant-system-competitor-analysis-ei6tos migration a7c31f08d442 creates dining_tables, modifier_groups, modifier_options - the same tables created by #29 (20b062b46d2c, 8476617278b8); applying both raises DuplicateTable even with a single head
- **hard-coded table-count test**：tests/test_restaurant_api.py:87 on default asserts len(Base.metadata.tables)==33. #29 -> 41 (+8), #45 -> 36 (+3), #17 -> 36 (+3); #12 (+6) and #26 (+4) did not bump it; each PR edits the same lines so they conflict textually AND the combined number must be recomputed (33+6+3+4+8+3 = 57 if all five chains merge)
- **edit of an already-applied migration**：claude/launch-wheel-game-campaign-t7octp modifies 6c120a022a19 (phase1_closed_loop...) to add CREATE EXTENSION citext; existing databases never re-run it, fresh databases need it

## 依序合併模擬（`git merge-tree`，不動工作目錄）

| 情境 | 順序 | 結果 |
|---|---|---|
| S1_ship_1013__31_then_29 | #31 → #29 | 全程無衝突；最終 head：`b7d2e9a41f05` |
| S1b_ship_1013__29_then_31 | #29 → #31 | 全程無衝突；最終 head：`b7d2e9a41f05` |
| S2_plus_odoo__31_29_45 | #31 → #29 → #45 | 在 #45 衝突：`tests/test_restaurant_api.py` |
| S3_core_and_defer_chain__ranks_1_to_11 | #31 → #29 → #45 → #11 → #12 → #14 → #20 → #21 → #22 → #17 → #26 | 在 #45 衝突：`tests/test_restaurant_api.py` |
| S4_all_31_in_recommended_order | #31 → #29 → #45 → #11 → #12 → #14 → #20 → #21 → #22 → #17 → #26 → #19 → #9 → #23 → #40 → #34 → #37 → #24 → #35 → #30 → #10 → #32 → #36 → #28 → #42 → #43 → #8 → #44 → #41 → #25 → #33 | 在 #45 衝突：`tests/test_restaurant_api.py` |

## 建議 PARK（離題）與可關閉

- **#8**（OFF_MISSION）：docs-only diff (docs/goodgle/cold_launch_strategy.md) whose subject is the Goodgle pet-ecosystem GTM, not the restaurant; zero restaurant_api/devswarm paths
- **#25**（OFF_MISSION）：license-plate selection platform (PlateGo); entire diff under plate_service/
- **#33**（OFF_MISSION）：license-plate agency platform; entire diff under car16/ (46 files)
- **#41**（OFF_MISSION）：government-subsidy radar pipeline: new top-level package + data + console script; named off-mission in the task statement
- **#44**（OFF_MISSION）：video-analysis tool (/watch-video: ffmpeg frames + transcript); "video tools" is named off-mission in the task statement

- **PR #38 (closed 2026-07-29) and PR #39 (closed 2026-08-13)**：ALREADY CLOSED - superseded by open PR #45
- **PR #25 (PlateGo) vs PR #33 (car16)**：SUGGEST: close #25 as duplicate concept of #33 (or park both)
- **membership.py LINE-broadcast hunk in #23, #43 (and variant in #17)**：NOT a PR closure: after #31 merges the hunk in #23/#43 is a no-op; drop #17's variant
- **Branch claude/restaurant-system-competitor-analysis-ei6tos (no PR)**：SUGGEST: never merge together with #29 (same tables)

## 分支分類（59 條）

| 類別 | 數量 |
|---|---:|
| DEFAULT_BRANCH | 1 |
| BOT_OR_REPAIR | 1 |
| HAS_OPEN_PR | 31 |
| ORPHAN_WITH_UNIQUE_COMMITS | 16 |
| STALE_NO_UNIQUE_WORK | 3 |
| MERGED_ALREADY | 7 |

`ORPHAN_WITH_UNIQUE_COMMITS` = 有獨有 commit、卻不在任何 PR 也不在預設分支，**其中的工作目前不會被合併進任何地方**，需要創辦人決定「開 PR 或封存」。

