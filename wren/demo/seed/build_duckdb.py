"""從 seed/*.csv 重建 wren/demo/data.duckdb(型別對齊 MDL)。"""
import pathlib
import duckdb

seed = pathlib.Path(__file__).resolve().parent
proj = seed.parent
db = proj / "data.duckdb"
con = duckdb.connect(str(db))
con.execute("CREATE OR REPLACE TABLE stores(store_id INTEGER, name VARCHAR, city VARCHAR)")
con.execute(f"INSERT INTO stores SELECT * FROM read_csv_auto('{seed/'stores.csv'}', header=true)")
con.execute("CREATE OR REPLACE TABLE orders(order_id INTEGER, store_id INTEGER, order_date DATE, total INTEGER)")
con.execute(f"INSERT INTO orders SELECT * FROM read_csv_auto('{seed/'orders.csv'}', header=true)")
con.close()
print(f"built {db}")
