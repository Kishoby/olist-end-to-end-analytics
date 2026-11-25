"""02_analysis.py - statistical analysis behind the dashboard story.

Question: does late delivery hurt customer satisfaction, and by how much? Where is lateness concentrated?
Output  : SQL table analysis_stat_tests, CSV/PNG files in outputs/
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats
from config import get_engine, OUT_DIR

eng = get_engine()
o = pd.read_sql("""SELECT order_id, MAX(customer_id) AS customer_id, MAX(review_score) AS review_score,
                          MAX(is_late) AS is_late, MAX(delay_days) AS delay_days, MAX(purchase_date_key) AS purchase_date_key
                   FROM fact_order_items WHERE is_late IS NOT NULL GROUP BY order_id""", eng)
st = pd.read_sql("SELECT customer_id, state FROM dim_customer", eng)
o = o.merge(st, on="customer_id").dropna(subset=["review_score"])
o["month"] = pd.to_datetime(o.purchase_date_key.astype(str), format="%Y%m%d").dt.to_period("M").dt.to_timestamp()
late, ontime = o[o.is_late == 1].review_score, o[o.is_late == 0].review_score

# 1) is the gap real? Mann-Whitney U (scores are ordinal 1-5) + effect size + bootstrap CI of the mean gap
u, p = stats.mannwhitneyu(late, ontime, alternative="two-sided")
cliffs = 2 * u / (len(late) * len(ontime)) - 1          # P(late > ontime) - P(late < ontime)
rng = np.random.default_rng(42)
boot = [rng.choice(late, len(late)).mean() - rng.choice(ontime, len(ontime)).mean() for _ in range(2000)]
lo, hi = np.percentile(boot, [2.5, 97.5])
# 2) share of 1-star reviews
tab = pd.crosstab(o.is_late, o.review_score == 1)
chi2, p_chi, _, _ = stats.chi2_contingency(tab)
one_star = tab[True] / tab.sum(axis=1)

res = pd.DataFrame([
    ("orders analysed (delivered, with review)", len(o)),
    ("late share of orders", float(o.is_late.mean())),
    ("mean review score - on time", float(ontime.mean())),
    ("mean review score - late", float(late.mean())),
    ("mean gap (late - on time)", float(late.mean() - ontime.mean())),
    ("mean gap 95% bootstrap CI low", float(lo)),
    ("mean gap 95% bootstrap CI high", float(hi)),
    ("Mann-Whitney U p-value", float(p)),
    ("Cliff's delta (effect size)", float(cliffs)),
    ("1-star share - on time", float(one_star[0])),
    ("1-star share - late", float(one_star[1])),
    ("chi-square p-value (1-star vs late)", float(p_chi)),
], columns=["test", "value"])
print(res.to_string(index=False))
res.to_sql("analysis_stat_tests", eng, if_exists="replace", index=False)

# 3) where is lateness concentrated?
by_state = (o.groupby("state").agg(orders=("order_id", "size"), late_rate=("is_late", "mean"), avg_review=("review_score", "mean"))
              .query("orders >= 500").sort_values("late_rate", ascending=False))
by_month = o[o.month >= "2017-01-01"].groupby("month").agg(orders=("order_id", "size"), late_rate=("is_late", "mean"),
                                                           avg_review=("review_score", "mean"))
bucket = pd.cut(o.delay_days, [-1000, 0, 3, 7, 1000], labels=["on time/early", "1-3 days late", "4-7 days late", "8+ days late"])
by_bucket = o.groupby(bucket, observed=True).agg(orders=("order_id", "size"), avg_review=("review_score", "mean"))
print("\nLATE RATE BY STATE (top 6)\n", by_state.head(6).round(3).to_string())
print("\nREVIEW BY DELAY BUCKET\n", by_bucket.round(3).to_string())
by_state.to_csv(OUT_DIR / "late_rate_by_state.csv"); by_month.to_csv(OUT_DIR / "late_rate_by_month.csv")
by_bucket.to_csv(OUT_DIR / "review_by_delay_bucket.csv")

fig, ax = plt.subplots(1, 3, figsize=(15, 4))
by_month.late_rate.plot(ax=ax[0], marker="o"); ax[0].set_title("Late delivery rate by month")
by_state.late_rate.head(10).plot.bar(ax=ax[1]); ax[1].set_title("Highest late rate by state (>=500 orders)")
by_bucket.avg_review.plot.bar(ax=ax[2], color="tab:orange"); ax[2].set_title("Average review by delivery delay"); ax[2].set_ylim(0, 5)
plt.tight_layout(); plt.savefig(OUT_DIR / "analysis_overview.png", dpi=130); plt.close()
