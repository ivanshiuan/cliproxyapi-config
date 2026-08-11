# 業務規則(給 LLM 產 SQL 時遵守)

- 「營收 / revenue」一律用 `SUM(orders.total)`,不要用其他欄位近似。
- 「訂單數」用 `COUNT(orders.order_id)`。
- 門市請用 `stores.name`(中文店名)呈現,不要只回 store_id。
- 日期欄 `orders.order_date` 已是 DATE 型別,可直接比較。
