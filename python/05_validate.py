"""05_validate.py - output validation. Fails loudly if any check does not pass.

A  ETL        : row counts and revenue vs the original CSV files
B  Star schema: keys, orphans, control totals, SQL result vs an independent pandas recomputation
C  Features   : one row per order, no leakage columns, history feature re-computed by brute force
D  Model      : beats the baseline on unseen months, risk bands rank correctly, metrics reproducible from saved predictions
E  Power BI   : writes the exact numbers the dashboard cards must show (outputs/powerbi_reconciliation.csv)
"""
import sys
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from importlib import import_module
from config import get_engine, DATA_DIR, OUT_DIR, EXPECTED_ROWS, EXPECTED_ITEM_REVENUE, TRAIN_END

fe = import_module("03_feature_engineering")
eng = get_engine()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def q(sql, **kw):
    return pd.read_sql(sql, eng, **kw)


# ---------------------------------------------------------------- A. ETL
for table, fname in [("customers", "olist_customers_dataset.csv"), ("orders", "olist_orders_dataset.csv"),
                     ("order_items", "olist_order_items_dataset.csv"), ("payments", "olist_order_payments_dataset.csv"),
                     ("reviews", "olist_order_reviews_dataset.csv"), ("products", "olist_products_dataset.csv"),
                     ("sellers", "olist_sellers_dataset.csv")]:
    n = int(q(f"SELECT COUNT(*) AS n FROM {table}").n.iloc[0])
    check(f"A rows {table}", n == EXPECTED_ROWS[fname], f"({n:,})")
rev = float(q("SELECT SUM(price) AS r FROM order_items").r.iloc[0])
check("A item revenue = Kaggle total", abs(rev - EXPECTED_ITEM_REVENUE) < 0.5, f"({rev:,.2f})")

# ------------------------------------------------------- B. star schema
fact = q("SELECT COUNT(*) AS n, SUM(price) AS r, COUNT(DISTINCT order_id) AS o FROM fact_order_items").iloc[0]
check("B fact rows = order items", int(fact.n) == EXPECTED_ROWS["olist_order_items_dataset.csv"], f"({int(fact.n):,})")
check("B fact revenue = order items revenue", abs(float(fact.r) - EXPECTED_ITEM_REVENUE) < 0.5)
for dim, key, fk in [("dim_product", "product_id", "product_id"), ("dim_seller", "seller_id", "seller_id"),
                     ("dim_customer", "customer_id", "customer_id"), ("dim_date", "date_key", "purchase_date_key")]:
    orphans = int(q(f"SELECT COUNT(*) AS n FROM fact_order_items f LEFT JOIN {dim} d ON d.{key} = f.{fk} "
                    f"WHERE d.{key} IS NULL").n.iloc[0])
    check(f"B no orphans fact -> {dim}", orphans == 0, f"({orphans})")
# independent recomputation straight from the raw CSV (different code path from the SQL)
o = pd.read_csv(DATA_DIR / "olist_orders_dataset.csv", parse_dates=["order_delivered_customer_date", "order_estimated_delivery_date"])
d = o[(o.order_status == "delivered") & o.order_delivered_customer_date.notna()]
late_csv = ((d.order_delivered_customer_date.dt.normalize() - d.order_estimated_delivery_date.dt.normalize()).dt.days > 0)
sql = q("""SELECT COUNT(DISTINCT order_id) AS n,
                  COUNT(DISTINCT CASE WHEN is_late = 1 THEN order_id END) * 1.0 / COUNT(DISTINCT order_id) AS late
           FROM fact_order_items WHERE is_late IS NOT NULL AND order_status = 'delivered'""").iloc[0]
check("B delivered orders: SQL = CSV", int(sql.n) == len(d), f"({int(sql.n):,})")
check("B late rate: SQL = CSV", abs(float(sql.late) - late_csv.mean()) < 0.0005,
      f"(SQL {float(sql.late):.4f} vs CSV {late_csv.mean():.4f})")

# ---------------------------------------------------------- C. features
f = q("SELECT * FROM ml_features", parse_dates=["purchase_ts"])
check("C one row per order", f.order_id.is_unique, f"({len(f):,})")
check("C rows = delivered orders with a date", len(f) == len(d))
check("C target has no nulls and is 0/1", f[fe.TARGET].isin([0, 1]).all())
check("C no forbidden (post-delivery) feature", not set(fe.FEATURES) & set(fe.FORBIDDEN))
check("C no feature is constant", (f[fe.FEATURES].nunique(dropna=True) > 1).all())
prior = f.loc[f.purchase_ts < pd.Timestamp(TRAIN_END), fe.TARGET].mean()
fa = q("SELECT order_id, delivered_ts FROM fact_order_items WHERE delivered_ts IS NOT NULL", parse_dates=["delivered_ts"]) \
    .drop_duplicates("order_id")
g = f.merge(fa, on="order_id")
rng = np.random.default_rng(1)
bad = 0
for i in rng.choice(len(g), 200, replace=False):
    r = g.iloc[i]
    h = g[(g.primary_seller_id == r.primary_seller_id) & (g.delivered_ts < r.purchase_ts)]
    exp = (h[fe.TARGET].sum() + fe.K_SMOOTH * prior) / (len(h) + fe.K_SMOOTH)
    bad += abs(exp - r.seller_prior_late_rate) > 1e-9
check("C seller history feature = brute force (200 random orders)", bad == 0, f"({bad} mismatches)")

# ------------------------------------------------------------- D. model
p = q("SELECT * FROM ml_late_predictions", parse_dates=["purchase_ts"])
check("D every feature row has a prediction", len(p) == len(f) and p.order_id.is_unique)
check("D probabilities in [0,1]", p.predicted_prob.between(0, 1).all())
te = p[p.data_split == "test"]
auc = roc_auc_score(te.actual_late, te.predicted_prob)
check("D test ROC-AUC clearly above chance (>= 0.60)", auc >= 0.60, f"({auc:.3f})")
m = q("SELECT * FROM ml_model_metrics")
saved = float(m[(m.model == "LightGBM") & (m.data_split == "test") & (m.metric == "roc_auc")].value.iloc[0])
check("D saved AUC reproducible from saved predictions", abs(saved - auc) < 1e-6)
rate = te.groupby("risk_band").actual_late.mean()
check("D High > Medium > Low late rate on test", rate["High"] > rate["Medium"] > rate["Low"],
      f"(H {rate['High']:.3f} / M {rate['Medium']:.3f} / L {rate['Low']:.3f})")
splits = p
check("D chronological split (train < valid < test)",
      splits.loc[splits.data_split == "train", "purchase_ts"].max() < splits.loc[splits.data_split == "valid", "purchase_ts"].min()
      <= splits.loc[splits.data_split == "valid", "purchase_ts"].max() < splits.loc[splits.data_split == "test", "purchase_ts"].min())

# ---------------------------------------- E. numbers for the Power BI cards
fx = q("""SELECT order_id, customer_unique_id, order_status, price, freight_value, item_total, is_late,
                 delivery_days, review_score FROM fact_order_items""")
dl = fx[fx.order_status == "delivered"]
per_order = fx.groupby("order_id").agg(rs=("review_score", "max"), dd=("delivery_days", "max"))
recon = pd.DataFrame([
    ("Revenue", dl.price.sum()), ("Orders", dl.order_id.nunique()), ("Customers", dl.customer_unique_id.nunique()),
    ("AOV", dl.price.sum() / dl.order_id.nunique()),
    ("Freight %", fx.freight_value.sum() / fx.item_total.sum()),
    ("Late Delivery %", fx[fx.is_late == 1].order_id.nunique() / fx[fx.is_late.isin([0, 1])].order_id.nunique()),
    ("Avg Review", per_order.rs.mean()), ("Avg Delivery Days", per_order.dd.mean()),
    ("Repeat Customers", int((dl.groupby("customer_unique_id").order_id.nunique() > 1).sum())),
], columns=["power_bi_measure", "expected_value"])
recon.to_csv(OUT_DIR / "powerbi_reconciliation.csv", index=False)
print("\nPower BI cards must show (no filters applied):\n" + recon.round(4).to_string(index=False))

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
