"""01_etl.py - EXTRACT, TRANSFORM, LOAD

Extract : read the 9 Olist CSVs and check row counts against the known Kaggle totals
Transform: fix types, rename columns, validate keys, aggregate geolocation to one row per zip prefix
Load    : write clean typed tables to SQL Server and log what was loaded

Tables created: customers, sellers, orders, order_items, payments, reviews,
                products, category_translation, geo_zip, etl_run_log
"""
import sys
import datetime as dt
import numpy as np
import pandas as pd
from sqlalchemy.types import String, Unicode, Integer, Float, DateTime, Numeric

from config import DATA_DIR, EXPECTED_ROWS, EXPECTED_ITEM_REVENUE, LOAD_REVIEW_TEXT, get_engine

ID = String(50)


def extract():
    print("EXTRACT")
    raw = {}
    for fname, expected in EXPECTED_ROWS.items():
        path = DATA_DIR / fname
        if not path.exists():
            sys.exit(f"Missing file: {path}  (set OLIST_DATA_DIR)")
        df = pd.read_csv(path, encoding="utf-8-sig")   # utf-8-sig removes the hidden BOM
        status = "OK" if len(df) == expected else f"MISMATCH (expected {expected:,})"
        print(f"  {fname:42s} {len(df):>10,} rows  {status}")
        if len(df) != expected:
            sys.exit("Row count differs from the Kaggle original - re-download the dataset.")
        raw[fname] = df
    return raw


def transform(raw):
    print("TRANSFORM")
    t = {}

    # --- customers / sellers -------------------------------------------------
    c = raw["olist_customers_dataset.csv"].copy()
    c["customer_city"] = c["customer_city"].str.strip()
    c["customer_state"] = c["customer_state"].str.strip().str.upper()
    t["customers"] = c

    s = raw["olist_sellers_dataset.csv"].copy()
    s["seller_city"] = s["seller_city"].str.strip()
    s["seller_state"] = s["seller_state"].str.strip().str.upper()
    t["sellers"] = s

    # --- orders ---------------------------------------------------------------
    o = raw["olist_orders_dataset.csv"].copy()
    for src, dst in [("order_purchase_timestamp", "purchase_ts"), ("order_approved_at", "approved_ts"),
                     ("order_delivered_carrier_date", "carrier_ts"),
                     ("order_delivered_customer_date", "delivered_ts"),
                     ("order_estimated_delivery_date", "estimated_ts")]:
        o[dst] = pd.to_datetime(o[src], errors="coerce")
    o = o.drop(columns=[c for c in o.columns if c.startswith("order_") and c != "order_id" and c != "order_status"])
    t["orders"] = o

    # --- order items -----------------------------------------------------------
    i = raw["olist_order_items_dataset.csv"].copy()
    i["shipping_limit_ts"] = pd.to_datetime(i["shipping_limit_date"], errors="coerce")
    i = i.drop(columns=["shipping_limit_date"])
    i["price"] = i["price"].round(2)
    i["freight_value"] = i["freight_value"].round(2)
    t["order_items"] = i

    # --- payments --------------------------------------------------------------
    t["payments"] = raw["olist_order_payments_dataset.csv"].copy()

    # --- reviews ---------------------------------------------------------------
    r = raw["olist_order_reviews_dataset.csv"].copy()
    r = r.rename(columns={"review_comment_title": "comment_title", "review_comment_message": "comment_message"})
    r["creation_ts"] = pd.to_datetime(r["review_creation_date"], errors="coerce")
    r["answer_ts"] = pd.to_datetime(r["review_answer_timestamp"], errors="coerce")
    r["has_comment"] = r["comment_message"].notna().astype(int)
    r = r.drop(columns=["review_creation_date", "review_answer_timestamp"])
    if not LOAD_REVIEW_TEXT:           # free text is not used here; skipping it keeps the SQL Server load fast and safe
        r = r.drop(columns=["comment_title", "comment_message"])
    t["reviews"] = r

    # --- products + category translation ----------------------------------------
    p = raw["olist_products_dataset.csv"].rename(columns={
        "product_category_name": "category_pt", "product_name_lenght": "name_length",
        "product_description_lenght": "description_length", "product_photos_qty": "photos_qty",
        "product_weight_g": "weight_g", "product_length_cm": "length_cm",
        "product_height_cm": "height_cm", "product_width_cm": "width_cm"})
    for col in ["name_length", "description_length", "photos_qty", "weight_g", "length_cm", "height_cm", "width_cm"]:
        p[col] = p[col].astype("Int64")
    t["products"] = p

    tr = raw["product_category_name_translation.csv"].rename(
        columns={"product_category_name": "category_pt", "product_category_name_english": "category_en"})
    # two categories in the products file have no translation in the original file
    extra = pd.DataFrame({"category_pt": ["pc_gamer", "portateis_cozinha_e_preparadores_de_alimentos"],
                          "category_en": ["pc_gamer", "portable_kitchen_and_food_preparers"]})
    tr = pd.concat([tr, extra[~extra.category_pt.isin(tr.category_pt)]], ignore_index=True)
    t["category_translation"] = tr

    # --- geolocation: 1M rows -> one row per zip prefix --------------------------
    g = raw["olist_geolocation_dataset.csv"].copy()
    g = g[g.geolocation_lat.between(-34.0, 6.0) & g.geolocation_lng.between(-74.0, -34.0)]  # inside Brazil
    geo = (g.groupby("geolocation_zip_code_prefix")
             .agg(lat=("geolocation_lat", "mean"), lng=("geolocation_lng", "mean"), n_points=("geolocation_lat", "size"))
             .reset_index().rename(columns={"geolocation_zip_code_prefix": "zip_prefix"}))
    t["geo_zip"] = geo

    # --- data quality checks ------------------------------------------------------
    print("  data quality checks")
    problems = []
    for name, key in [("orders", "order_id"), ("customers", "customer_id"),
                      ("products", "product_id"), ("sellers", "seller_id")]:
        dups = int(t[name][key].duplicated().sum())
        print(f"    duplicate {name}.{key}: {dups}")
        if dups:
            problems.append(f"{name}.{key} has {dups} duplicates")
    orphan_items = int((~t["order_items"].order_id.isin(t["orders"].order_id)).sum())
    orphan_orders = int((~t["orders"].customer_id.isin(t["customers"].customer_id)).sum())
    orphan_products = int((~t["order_items"].product_id.isin(t["products"].product_id)).sum())
    orphan_sellers = int((~t["order_items"].seller_id.isin(t["sellers"].seller_id)).sum())
    print(f"    orphan order_items->orders: {orphan_items}, orders->customers: {orphan_orders}, "
          f"items->products: {orphan_products}, items->sellers: {orphan_sellers}")
    problems += [f"{n} orphan rows" for n, v in [("items->orders", orphan_items), ("orders->customers", orphan_orders),
                                                  ("items->products", orphan_products), ("items->sellers", orphan_sellers)] if v]
    rev = float(t["order_items"].price.sum())
    print(f"    item revenue: {rev:,.2f} (expected {EXPECTED_ITEM_REVENUE:,.2f})")
    if abs(rev - EXPECTED_ITEM_REVENUE) > 1:
        problems.append("item revenue differs from the known total")
    # known quirks - logged, not fatal
    print(f"    known quirks: {int(t['reviews'].order_id.duplicated().sum())} extra reviews per order, "
          f"{int(t['payments'].order_id.duplicated().sum())} extra payment rows, "
          f"{int((t['orders'].order_status == 'delivered').sum() - t['orders'].loc[t['orders'].order_status == 'delivered', 'delivered_ts'].notna().sum())} "
          f"'delivered' orders with no delivery date")
    if problems:
        sys.exit("ETL stopped: " + "; ".join(problems))
    return t


def load(tables, engine):
    print("LOAD")
    dtypes = {
        "customers": {"customer_id": ID, "customer_unique_id": ID, "customer_city": Unicode(100),
                      "customer_state": String(5), "customer_zip_code_prefix": Integer()},
        "sellers": {"seller_id": ID, "seller_city": Unicode(100), "seller_state": String(5),
                    "seller_zip_code_prefix": Integer()},
        "orders": {"order_id": ID, "customer_id": ID, "order_status": String(20), "purchase_ts": DateTime(),
                   "approved_ts": DateTime(), "carrier_ts": DateTime(), "delivered_ts": DateTime(),
                   "estimated_ts": DateTime()},
        "order_items": {"order_id": ID, "product_id": ID, "seller_id": ID, "shipping_limit_ts": DateTime(),
                        "price": Numeric(10, 2), "freight_value": Numeric(10, 2)},
        "payments": {"order_id": ID, "payment_type": String(30), "payment_value": Numeric(10, 2)},
        "reviews": {"review_id": ID, "order_id": ID, "comment_title": Unicode(500), "comment_message": Unicode(),
                    "creation_ts": DateTime(), "answer_ts": DateTime()},   # text columns only exist if LOAD_REVIEW_TEXT=1
        "products": {"product_id": ID, "category_pt": String(100)},
        "category_translation": {"category_pt": String(100), "category_en": String(100)},
        "geo_zip": {"zip_prefix": Integer(), "lat": Float(), "lng": Float(), "n_points": Integer()},
    }
    log = []
    run_ts = dt.datetime.now().replace(microsecond=0)
    for name, df in tables.items():
        df.to_sql(name, engine, if_exists="replace", index=False, chunksize=5000, dtype=dtypes.get(name))
        n_db = int(pd.read_sql(f"SELECT COUNT(*) AS n FROM {name}", engine).n.iloc[0])
        ok = "OK" if n_db == len(df) else "MISMATCH"
        print(f"  {name:22s} {len(df):>9,} rows -> {n_db:>9,} in DB  {ok}")
        if n_db != len(df):
            sys.exit(f"Load check failed for {name}")
        log.append({"table_name": name, "rows_loaded": n_db, "run_ts": run_ts})
    pd.DataFrame(log).to_sql("etl_run_log", engine, if_exists="append", index=False)


if __name__ == "__main__":
    eng = get_engine()
    load(transform(extract()), eng)
    print("ETL finished.")
