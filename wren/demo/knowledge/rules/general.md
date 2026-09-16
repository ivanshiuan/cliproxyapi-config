# 業務規則(給 LLM 產 SQL 時遵守)

- 金額單位:新台幣(TWD),`orders.total` 與所有營收數字都以「元」為單位;金額型別為 DECIMAL,不要當浮點數處理。
- 「營收 / revenue」一律用 `SUM(orders.total)`,不要用其他欄位近似。
- 「訂單數」用 `COUNT(orders.order_id)`。
- 門市請用 `stores.name`(中文店名)呈現,不要只回 store_id。
- 日期欄 `orders.order_date` 已是 DATE 型別,可直接比較。
