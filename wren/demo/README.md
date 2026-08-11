# WrenAI 餐飲 demo 專案

一個**自足、零外部依賴**的 WrenAI 範例:用中文問資料 → Wren 透過語意層(MDL)
轉成受治理的 SQL → 打進本地 DuckDB → 回答案。當作學習 / 擴充 Wren 用法的沙盒,
之後接真實資料庫時照這個結構複製即可。

## 一鍵跑

```bash
make wren-install      # 首次 / 新容器:裝 wren CLI(見 scripts/setup_wren.sh)
make wren-demo         # 重建資料 + 編譯 MDL + 跑「每家店營收」範例查詢
```

預期輸出:

```
store   city  orders revenue
逢甲店   台中     2     3090
信義店   台北     3     2730
西門店   台北     1      430
```

## 自己問

```bash
cd wren/demo
CONN='{"datasource":"duckdb","url":"'"$PWD"'","format":"duckdb"}'
wren --sql "SELECT city, SUM(total) AS revenue FROM orders o JOIN stores s ON o.store_id=s.store_id GROUP BY city ORDER BY revenue DESC" \
  --connection-info "$CONN" -o table
```

## 檔案結構

| 路徑 | 作用 |
|---|---|
| `seed/stores.csv`、`seed/orders.csv` | **唯一真相源**(人可讀、可編輯的種子資料) |
| `seed/build_duckdb.py` | 從 CSV 重建 `data.duckdb`(型別對齊 MDL) |
| `wren_project.yml` | 專案設定(`data_source: duckdb`) |
| `models/*/metadata.yml` | 模型 = 資料表的語意定義(欄位、型別、主鍵) |
| `relationships.yml` | 表間關聯(`orders` 多對一 `stores`) |
| `knowledge/rules/general.md` | **治理層**:給 LLM 產 SQL 時要遵守的業務規則 |
| `build.sh` | 重建資料 + `wren context build` |
| `data.duckdb`、`target/` | 產物,**不進版控**(見 `.gitignore`,由 `build.sh` 重生) |

## 換成真實資料庫怎麼做

1. `wren_project.yml` 的 `data_source` 改成 `postgres`(或 mysql/bigquery…)。
2. 連線帳密走 `.env`(**永不進 git**;Wren 的安全模型:你填、agent 看不到值)。
3. 模型不用手寫 —— 叫 Claude 用 `/wren` 技能跑 `wren skills get generate-mdl`,
   它會探索 schema 自動產 `models/`。
4. `wren context build` → 開始問。

完整說明見 `docs/20_wrenai_setup.md`。
