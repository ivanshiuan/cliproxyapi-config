"""從 seed/*.csv 重建 wren/demo/data.duckdb。

型別對齊 MDL;金額欄用 DECIMAL(呼應帝國鐵律「所有金錢用 Decimal,永不用 float」)。
用 DuckDB replacement scan(以變數名參照 relation)載入 CSV,不把路徑內插進 SQL。
"""

import pathlib

import duckdb

seed = pathlib.Path(__file__).resolve().parent
proj = seed.parent
db = proj / "data.duckdb"

con = duckdb.connect(str(db))
stores_csv = con.read_csv(str(seed / "stores.csv"), header=True)
orders_csv = con.read_csv(str(seed / "orders.csv"), header=True)
con.execute("CREATE OR REPLACE TABLE stores(store_id INTEGER, name VARCHAR, city VARCHAR)")
con.execute("INSERT INTO stores SELECT * FROM stores_csv")
con.execute(
    "CREATE OR REPLACE TABLE orders("
    "order_id INTEGER, store_id INTEGER, order_date DATE, total DECIMAL(14, 2))"
)
con.execute("INSERT INTO orders SELECT * FROM orders_csv")
con.close()
print(f"built {db.name}")
