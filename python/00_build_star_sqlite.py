"""00_build_star_sqlite.py - OPTIONAL stand-in for sql/02_star_schema.sql

Builds the same star schema (dim_date, dim_customer, dim_product, dim_seller, fact_order_items)
with pandas, so the Python steps can be run without SQL Server (for example on Mac/Linux, or in tests).
With SQL Server, skip this file and run sql/02_star_schema.sql instead.
"""
import numpy as np
import pandas as pd
from config import get_engine

eng = get_engine()
ts_cols = ["purchase_ts", "approved_ts", "carrier_ts", "delivered_ts", "estimated_ts"]
orders = pd.read_sql("SELECT * FROM orders", eng, parse_dates=ts_cols)
items = pd.read_sql("SELECT * FROM order_items", eng)
pay = pd.read_sql("SELECT * FROM payments", eng)
rev = pd.read_sql("SELECT * FROM reviews", eng, parse_dates=["answer_ts"])
cust = pd.read_sql("SELECT * FROM customers", eng)
sell = pd.read_sql("SELECT * FROM sellers", eng)
prod = pd.read_sql("SELECT * FROM products", eng)
tr = pd.read_sql("SELECT * FROM category_translation", eng)

# dimensions
days = pd.date_range("2016-09-01", "2018-12-31")
dim_date = pd.DataFrame({"full_date": days})
dim_date["date_key"] = days.year * 10000 + days.month * 100 + days.day
dim_date["yr"], dim_date["qtr"], dim_date["mth"] = days.year, days.quarter, days.month
dim_date["month_name"], dim_date["day_name"] = days.month_name(), days.day_name()
dim_date["month_start"] = days.to_period("M").to_timestamp()
dim_date["day_num"] = (days.dayofweek + 1) % 7 + 1

dim_customer = cust.rename(columns={"customer_zip_code_prefix": "zip_prefix", "customer_city": "city",
                                    "customer_state": "state"})
dim_seller = sell.rename(columns={"seller_zip_code_prefix": "zip_prefix", "seller_city": "city",
                                  "seller_state": "state"})
dim_product = prod.merge(tr, on="category_pt", how="left")
dim_product["category"] = dim_product.category_en.fillna(dim_product.category_pt).fillna("unknown")
dim_product["volume_cm3"] = dim_product.length_cm * dim_product.height_cm * dim_product.width_cm
dim_product = dim_product[["product_id", "category", "name_length", "description_length", "photos_qty",
                           "weight_g", "volume_cm3"]]

# fact (item grain; payments and reviews summarised per order)
pg = pay.groupby("order_id").agg(max_installments=("payment_installments", "max"),
                                 payment_methods=("payment_type", "nunique")).reset_index()
first_pay = pay[pay.payment_sequential == 1][["order_id", "payment_type"]].drop_duplicates("order_id") \
    .rename(columns={"payment_type": "primary_payment_type"})
pg = pg.merge(first_pay, on="order_id", how="left")
rv = rev.sort_values(["order_id", "answer_ts", "review_id"], ascending=[True, False, True]) \
    .drop_duplicates("order_id")[["order_id", "review_score"]]

f = (items.merge(orders, on="order_id").merge(cust[["customer_id", "customer_unique_id"]], on="customer_id")
          .merge(pg, on="order_id", how="left").merge(rv, on="order_id", how="left"))
f["purchase_date_key"] = f.purchase_ts.dt.year * 10000 + f.purchase_ts.dt.month * 100 + f.purchase_ts.dt.day
f["item_total"] = f.price + f.freight_value
f["delivery_days"] = (f.delivered_ts.dt.normalize() - f.purchase_ts.dt.normalize()).dt.days
f["delay_days"] = (f.delivered_ts.dt.normalize() - f.estimated_ts.dt.normalize()).dt.days
f["is_late"] = np.where(f.delivered_ts.isna(), np.nan, (f.delay_days > 0).astype(float))
fact = f[["order_id", "order_item_id", "product_id", "seller_id", "customer_id", "customer_unique_id",
          "purchase_date_key", "order_status", "purchase_ts", "approved_ts", "carrier_ts", "delivered_ts",
          "estimated_ts", "price", "freight_value", "item_total", "delivery_days", "delay_days", "is_late",
          "primary_payment_type", "max_installments", "payment_methods", "review_score"]]

for name, df in [("dim_date", dim_date), ("dim_customer", dim_customer), ("dim_seller", dim_seller),
                 ("dim_product", dim_product), ("fact_order_items", fact)]:
    df.to_sql(name, eng, if_exists="replace", index=False, chunksize=5000)
    print(f"{name:18s} {len(df):>9,} rows")
print("fact revenue:", round(fact.price.sum(), 2))
