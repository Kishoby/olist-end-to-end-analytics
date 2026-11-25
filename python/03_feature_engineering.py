"""03_feature_engineering.py - build one row per delivered order with features known AT PURCHASE TIME.

Target : is_late = delivered on a later calendar day than the promised (estimated) date.
Rule   : a feature may only use information available when the customer placed the order.
         History features (seller / state late rate) use only orders already DELIVERED before that moment.
Output : SQL table  ml_features
"""
import numpy as np
import pandas as pd
from config import get_engine, TRAIN_END

K_SMOOTH = 20   # shrinks rates of sellers/states with little history toward the overall rate


def haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, [lat1, lng1, lat2, lng2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def prior_late_rate(df, group_col, prior, k=K_SMOOTH):
    """For each order: smoothed late rate of the same group among orders delivered BEFORE its purchase time."""
    n_prior = np.zeros(len(df))
    rate = np.full(len(df), prior)
    pos = {idx: i for i, idx in enumerate(df.index)}
    for _, g in df.groupby(group_col):
        d = g.dropna(subset=["delivered_ts"]).sort_values("delivered_ts")
        if d.empty:
            continue
        d_times = d["delivered_ts"].values.astype("datetime64[ns]").astype("int64")
        cum_late = np.cumsum(d["is_late"].values)
        p_times = g["purchase_ts"].values.astype("datetime64[ns]").astype("int64")
        cnt = np.searchsorted(d_times, p_times, side="left")          # deliveries completed before purchase
        late = np.where(cnt > 0, cum_late[np.maximum(cnt - 1, 0)], 0.0)
        rows = [pos[i] for i in g.index]
        n_prior[rows] = cnt
        rate[rows] = (late + k * prior) / (cnt + k)
    return n_prior, rate


def build_features(eng):
    parse = ["purchase_ts", "delivered_ts", "estimated_ts"]
    fact = pd.read_sql("""SELECT order_id, order_item_id, product_id, seller_id, customer_id, purchase_date_key,
                                 order_status, purchase_ts, delivered_ts, estimated_ts, price, freight_value,
                                 is_late, primary_payment_type, max_installments, payment_methods, review_score
                          FROM fact_order_items""", eng, parse_dates=parse)
    prod = pd.read_sql("SELECT product_id, category, photos_qty, weight_g, volume_cm3 FROM dim_product", eng)
    cust = pd.read_sql("SELECT customer_id, zip_prefix AS c_zip, state AS customer_state FROM dim_customer", eng)
    sell = pd.read_sql("SELECT seller_id, zip_prefix AS s_zip, state AS seller_state FROM dim_seller", eng)
    geo = pd.read_sql("SELECT zip_prefix, lat, lng FROM geo_zip", eng)

    fact = fact.merge(prod, on="product_id", how="left")
    # primary item = the most expensive item in the order (its seller / category represent the order)
    primary = (fact.sort_values(["order_id", "price", "order_item_id"], ascending=[True, False, True])
                   .drop_duplicates("order_id")[["order_id", "seller_id", "product_id", "category"]]
                   .rename(columns={"seller_id": "primary_seller_id", "product_id": "primary_product_id",
                                    "category": "main_category"}))
    o = fact.groupby("order_id").agg(
        customer_id=("customer_id", "first"), purchase_date_key=("purchase_date_key", "first"),
        order_status=("order_status", "first"), purchase_ts=("purchase_ts", "first"),
        delivered_ts=("delivered_ts", "first"), estimated_ts=("estimated_ts", "first"),
        is_late=("is_late", "first"), review_score=("review_score", "first"),
        n_items=("order_item_id", "count"), n_sellers=("seller_id", "nunique"), n_products=("product_id", "nunique"),
        total_price=("price", "sum"), total_freight=("freight_value", "sum"),
        max_item_price=("price", "max"), mean_item_price=("price", "mean"),
        total_weight_g=("weight_g", "sum"), max_volume_cm3=("volume_cm3", "max"), avg_photos_qty=("photos_qty", "mean"),
        primary_payment_type=("primary_payment_type", "first"), max_installments=("max_installments", "first"),
        payment_methods=("payment_methods", "first")).reset_index().merge(primary, on="order_id")

    # modelling population: delivered orders whose outcome is known
    o = o[(o.order_status == "delivered") & o.delivered_ts.notna()].copy()
    o["is_late"] = o["is_late"].astype(int)

    # basket / price features
    o["freight_ratio"] = o.total_freight / (o.total_price + o.total_freight)
    o["price_per_kg"] = o.total_price / (o.total_weight_g.replace(0, np.nan) / 1000)

    # geography
    o = (o.merge(cust, on="customer_id", how="left")
          .merge(sell.rename(columns={"seller_id": "primary_seller_id"}), on="primary_seller_id", how="left"))
    o = (o.merge(geo.rename(columns={"zip_prefix": "c_zip", "lat": "customer_lat", "lng": "customer_lng"}), on="c_zip", how="left")
          .merge(geo.rename(columns={"zip_prefix": "s_zip", "lat": "seller_lat", "lng": "seller_lng"}), on="s_zip", how="left"))
    o["distance_km"] = haversine_km(o.customer_lat, o.customer_lng, o.seller_lat, o.seller_lng)
    o["same_state"] = (o.customer_state == o.seller_state).astype(int)

    # calendar / promise
    o["purchase_hour"] = o.purchase_ts.dt.hour
    o["purchase_dow"] = o.purchase_ts.dt.dayofweek
    o["purchase_month"] = o.purchase_ts.dt.month
    o["is_weekend"] = (o.purchase_dow >= 5).astype(int)
    o["is_peak_season"] = o.purchase_month.isin([11, 12]).astype(int)
    o["promised_days"] = (o.estimated_ts - o.purchase_ts).dt.total_seconds() / 86400
    o["km_per_promised_day"] = o.distance_km / o.promised_days.replace(0, np.nan)

    # leakage-safe history features
    o = o.sort_values("purchase_ts").reset_index(drop=True)
    prior = o.loc[o.purchase_ts < pd.Timestamp(TRAIN_END), "is_late"].mean()   # overall rate from the TRAIN period only
    o["seller_prior_orders"], o["seller_prior_late_rate"] = prior_late_rate(o, "primary_seller_id", prior)
    o["state_prior_orders"], o["state_prior_late_rate"] = prior_late_rate(o, "customer_state", prior)

    # top-N category grouping keeps the model stable (rare categories -> 'other')
    top = o.loc[o.purchase_ts < pd.Timestamp(TRAIN_END), "main_category"].value_counts().head(25).index
    o["main_category_grp"] = np.where(o.main_category.isin(top), o.main_category, "other")
    return o


NUMERIC = ["n_items", "n_sellers", "n_products", "total_price", "total_freight", "max_item_price", "mean_item_price",
           "total_weight_g", "max_volume_cm3", "avg_photos_qty", "freight_ratio", "price_per_kg", "max_installments",
           "payment_methods", "distance_km", "same_state", "customer_lat", "customer_lng", "seller_lat", "seller_lng",
           "purchase_hour", "purchase_dow", "is_weekend", "is_peak_season", "promised_days",
           "km_per_promised_day", "seller_prior_late_rate", "state_prior_late_rate"]
CATEGORICAL = ["customer_state", "seller_state", "main_category_grp", "primary_payment_type"]
FEATURES = NUMERIC + CATEGORICAL
TARGET = "is_late"
KEEP_FOR_POWERBI = ["order_id", "customer_id", "primary_seller_id", "primary_product_id", "purchase_date_key",
                    "purchase_ts"]
# columns that must never be used as inputs (they are only known after delivery)
FORBIDDEN = ["delivered_ts", "review_score", "is_late", "delay_days", "delivery_days"]

if __name__ == "__main__":
    eng = get_engine()
    feats = build_features(eng)
    assert not set(FEATURES) & set(FORBIDDEN), "leakage: forbidden column used as a feature"
    out = feats[KEEP_FOR_POWERBI + FEATURES + [TARGET]]
    out.to_sql("ml_features", eng, if_exists="replace", index=False, chunksize=5000)
    print(f"ml_features: {len(out):,} rows x {out.shape[1]} columns ({len(FEATURES)} model features)")
    print(f"late rate overall: {out[TARGET].mean():.4f}")
    print(out[FEATURES].isna().mean().loc[lambda s: s > 0].round(4).to_string())
