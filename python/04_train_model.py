"""04_train_model.py - train, evaluate and explain the late-delivery model.

Split   : by purchase date (train < TRAIN_END <= valid < VALID_END <= test) - the model never sees the future
Models  : baseline (always the training late rate), logistic regression, LightGBM
Explain : SHAP + permutation importance
Output  : SQL tables ml_model_metrics, ml_feature_importance, ml_lift_table, ml_late_predictions
          plus charts in outputs/ and the model in models/
"""
import warnings
import numpy as np
import pandas as pd
import joblib
import lightgbm as lgb
import shap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (roc_auc_score, average_precision_score, brier_score_loss, log_loss,
                             precision_score, recall_score, RocCurveDisplay, PrecisionRecallDisplay)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from config import get_engine, OUT_DIR, MODEL_DIR, TRAIN_END, VALID_END
from importlib import import_module
fe = import_module("03_feature_engineering")
NUMERIC, CATEGORICAL, FEATURES, TARGET = fe.NUMERIC, fe.CATEGORICAL, fe.FEATURES, fe.TARGET
warnings.filterwarnings("ignore")
SEED = 42

eng = get_engine()
df = pd.read_sql("SELECT * FROM ml_features", eng, parse_dates=["purchase_ts"])
df["data_split"] = np.where(df.purchase_ts < pd.Timestamp(TRAIN_END), "train",
                    np.where(df.purchase_ts < pd.Timestamp(VALID_END), "valid", "test"))
tr, va, te = (df[df.data_split == s] for s in ["train", "valid", "test"])
print("SPLIT  " + "  ".join(f"{n}: {len(d):,} orders, late rate {d[TARGET].mean():.3f}" for n, d in
                            [("train", tr), ("valid", va), ("test", te)]))

# ---------------------------------------------------------------- models
base_rate = tr[TARGET].mean()

logit = Pipeline([
    ("prep", ColumnTransformer([
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), NUMERIC),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=50), CATEGORICAL)])),
    ("clf", LogisticRegression(max_iter=2000, C=0.5))])
logit.fit(tr[FEATURES], tr[TARGET])


def to_lgb(frame):
    x = frame[FEATURES].copy()
    for c in CATEGORICAL:
        x[c] = pd.Categorical(x[c], categories=sorted(df[c].dropna().unique()))
    return x


gbm = lgb.LGBMClassifier(n_estimators=1500, learning_rate=0.03, num_leaves=15, min_child_samples=200,
                         subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
                         random_state=SEED, verbose=-1)
gbm.fit(to_lgb(tr), tr[TARGET], eval_set=[(to_lgb(va), va[TARGET])], eval_metric="auc",
        callbacks=[lgb.early_stopping(100, verbose=False)])
print(f"LightGBM best iteration: {gbm.best_iteration_}")

scorers = {"baseline (training late rate)": lambda d: np.full(len(d), base_rate),
           "logistic regression": lambda d: logit.predict_proba(d[FEATURES])[:, 1],
           "LightGBM": lambda d: gbm.predict_proba(to_lgb(d))[:, 1]}


def decile_table(y, p):
    t = pd.DataFrame({"y": y.values, "p": p}).sort_values("p", ascending=False).reset_index(drop=True)
    t["decile"] = pd.qcut(t.index, 10, labels=False) + 1
    g = t.groupby("decile").agg(orders=("y", "size"), late_orders=("y", "sum"), avg_pred=("p", "mean")).reset_index()
    g["late_rate"] = g.late_orders / g.orders
    g["lift"] = g.late_rate / t.y.mean()
    g["cum_capture"] = g.late_orders.cumsum() / g.late_orders.sum()
    return g


metrics = []
for split_name, d in [("valid", va), ("test", te)]:
    for model_name, fn in scorers.items():
        p = fn(d)
        row = {"roc_auc": roc_auc_score(d[TARGET], p) if model_name != list(scorers)[0] else 0.5,
               "pr_auc": average_precision_score(d[TARGET], p), "brier": brier_score_loss(d[TARGET], p),
               "log_loss": log_loss(d[TARGET], np.clip(p, 1e-6, 1 - 1e-6))}
        if model_name != list(scorers)[0]:
            row["top_decile_lift"] = decile_table(d[TARGET], p).lift.iloc[0]
            row["top_30pct_capture"] = decile_table(d[TARGET], p).cum_capture.iloc[2]
        for k, v in row.items():
            metrics.append({"model": model_name, "data_split": split_name, "metric": k, "value": float(v)})
M = pd.DataFrame(metrics)
print("\nMETRICS")
print(M.pivot_table(index=["data_split", "model"], columns="metric", values="value").round(3).to_string())

# ------------------------------------------------------- scoring + risk bands
for name, d in [("train", tr), ("valid", va), ("test", te)]:
    df.loc[d.index, "predicted_prob"] = scorers["LightGBM"](d)
hi_cut = float(np.quantile(df.loc[df.data_split == "valid", "predicted_prob"], 0.90))
med_cut = float(np.quantile(df.loc[df.data_split == "valid", "predicted_prob"], 0.70))
df["risk_band"] = np.where(df.predicted_prob >= hi_cut, "High", np.where(df.predicted_prob >= med_cut, "Medium", "Low"))
df["actual_late"] = df[TARGET]
print(f"\nrisk band cut-offs (from validation): Medium >= {med_cut:.3f}, High >= {hi_cut:.3f}")
band = te.assign(band=df.loc[te.index, "risk_band"]).groupby("band")[TARGET].agg(["size", "mean"]).reindex(["High", "Medium", "Low"])
print("TEST late rate by risk band:\n", band.round(3).to_string())
hi = df[(df.data_split == "test") & (df.risk_band == "High")]
for k, v in {"high_band_precision_test": hi.actual_late.mean(),
             "high_band_recall_test": hi.actual_late.sum() / te[TARGET].sum(),
             "high_band_cutoff": hi_cut, "medium_band_cutoff": med_cut}.items():
    M = pd.concat([M, pd.DataFrame([{"model": "LightGBM", "data_split": "test", "metric": k, "value": float(v)}])])

# -------------------------------------------------------------- explanation
sample = te.sample(min(4000, len(te)), random_state=SEED)
Xs = to_lgb(sample)
sv = shap.TreeExplainer(gbm).shap_values(Xs)
sv = sv[1] if isinstance(sv, list) else sv
shap_imp = pd.Series(np.abs(sv).mean(0), index=FEATURES, name="mean_abs_shap")
perm = permutation_importance(gbm, Xs, sample[TARGET], scoring="roc_auc", n_repeats=5, random_state=SEED, n_jobs=1)
imp = pd.DataFrame({"feature": FEATURES, "mean_abs_shap": shap_imp.values,
                    "perm_auc_drop": perm.importances_mean}).sort_values("mean_abs_shap", ascending=False)
imp["rank"] = np.arange(1, len(imp) + 1)
print("\nTOP 12 FEATURES (SHAP / permutation AUC drop)")
print(imp.head(12).round(4).to_string(index=False))

# ------------------------------------------------------------------ charts
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
for name, fn in list(scorers.items())[1:]:
    RocCurveDisplay.from_predictions(te[TARGET], fn(te), name=name, ax=ax[0])
    PrecisionRecallDisplay.from_predictions(te[TARGET], fn(te), name=name, ax=ax[1])
ax[0].set_title("ROC (test)"); ax[1].set_title("Precision-recall (test)")
plt.tight_layout(); plt.savefig(OUT_DIR / "model_roc_pr_test.png", dpi=130); plt.close()
shap.summary_plot(sv, Xs, show=False, max_display=15)
plt.tight_layout(); plt.savefig(OUT_DIR / "shap_summary.png", dpi=130); plt.close()
lift = decile_table(te[TARGET], scorers["LightGBM"](te))
plt.figure(figsize=(6, 4)); plt.bar(lift.decile, lift.late_rate); plt.axhline(te[TARGET].mean(), ls="--", c="grey")
plt.xlabel("Risk decile (1 = highest predicted risk)"); plt.ylabel("Actual late rate"); plt.title("Lift by decile (test)")
plt.tight_layout(); plt.savefig(OUT_DIR / "lift_by_decile_test.png", dpi=130); plt.close()

# ------------------------------------------------------------------ save
joblib.dump({"model": gbm, "features": FEATURES, "categorical": CATEGORICAL, "hi_cut": hi_cut, "med_cut": med_cut},
            MODEL_DIR / "late_delivery_lgbm.joblib")
M.to_sql("ml_model_metrics", eng, if_exists="replace", index=False)
imp.to_sql("ml_feature_importance", eng, if_exists="replace", index=False)
lift.to_sql("ml_lift_table", eng, if_exists="replace", index=False)
pred = df[["order_id", "customer_id", "primary_seller_id", "primary_product_id", "purchase_date_key", "purchase_ts",
           "data_split", "predicted_prob", "risk_band", "actual_late"]]
pred.to_sql("ml_late_predictions", eng, if_exists="replace", index=False, chunksize=5000)
print(f"\nSaved: ml_model_metrics, ml_feature_importance, ml_lift_table, ml_late_predictions ({len(pred):,} rows), "
      f"model file, 3 charts")
