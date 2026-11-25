# Olist E-Commerce: Late-Delivery Analytics (SQL + Python + Power BI)

End-to-end analytics project on the Olist Brazilian e-commerce dataset (about 100k orders, 2016-2018).
It follows the full workflow: **ETL, SQL modelling, data analysis, feature engineering, machine learning,
output validation and a final dashboard.**

**Business question:** Do late deliveries hurt customer satisfaction, where do they happen, and can we flag
at-risk orders at the moment of purchase?

## Architecture

```
9 Kaggle CSVs
   |  python/01_etl.py          extract, validate, clean, type, load
   v
SQL Server: clean tables (orders, order_items, payments, reviews, customers, sellers, products, geo_zip ...)
   |  sql/02_star_schema.sql    transform into a star schema
   v
dim_date, dim_customer, dim_product, dim_seller, fact_order_items
   |-- sql/03_analysis_queries.sql     8 advanced queries (window functions, cohorts, RFM, Pareto)
   |-- python/02_analysis.py           statistical tests (Mann-Whitney, effect size, bootstrap CI)
   |-- python/03_feature_engineering.py   one row per order, 32 features known at purchase time
   |-- python/04_train_model.py        baseline vs logistic vs LightGBM, SHAP, risk bands
   v
ml_features, ml_late_predictions, ml_model_metrics, ml_feature_importance, ml_lift_table
   |-- python/05_validate.py + sql/05_ml_integration.sql    28 automated checks
   v
Power BI (see POWERBI_GUIDE.md): sales, customers, delivery, sellers, risk model, insights
```

## How to run

1. Install: Python 3.10+, SQL Server (Developer/Express) + ODBC Driver 17 or 18, SSMS, Power BI Desktop.
2. `pip install -r requirements.txt`
3. Put the 9 CSV files in `C:\olist` (or set `OLIST_DATA_DIR`). Create an empty database: `CREATE DATABASE OlistDB;`
4. If your SQL Server is not `localhost` with Windows login, set `OLIST_DB_URL` (see `python/config.py`).
5. `cd python` then `python run_pipeline.py` (ETL, star schema, analysis, features, model, validation).
6. In SSMS run `sql/03_analysis_queries.sql`, `sql/04_views_indexes_procs.sql`, `sql/05_ml_integration.sql`.
7. Build the report with `POWERBI_GUIDE.md`.

No SQL Server? `python run_pipeline.py --no-sql` with `OLIST_DB_URL=sqlite:///olist.db` runs everything except the
T-SQL files (the star schema is rebuilt in pandas by `00_build_star_sqlite.py`).

Step by step, when you want to run one stage at a time: `01_etl.py`, then `sql/01_data_quality.sql` and
`sql/02_star_schema.sql`, then `02_analysis.py`, `03_feature_engineering.py`, `04_train_model.py`, `05_validate.py`.

## What each stage does

| Stage | Files | What to explain in an interview |
|---|---|---|
| ETL | `python/01_etl.py` | Row counts checked against the Kaggle originals, key and orphan checks, revenue control total, geolocation (1M rows) reduced to one row per zip prefix with an in-Brazil filter, load verified table by table and logged to `etl_run_log` |
| Transform (SQL) | `sql/02_star_schema.sql` | Fact at order-item grain; payments and reviews summarised per order first so revenue is never duplicated; late flag defined on calendar days |
| Analysis | `sql/03_analysis_queries.sql`, `python/02_analysis.py` | Cohort retention, RFM, Pareto, ranking, funnel timing; significance test and effect size for the review gap |
| Feature engineering | `python/03_feature_engineering.py` | 32 features at order grain, all known at purchase time; seller and state late-rate history uses only orders already delivered before the purchase (leakage-safe, brute-force verified) |
| ML training | `python/04_train_model.py` | Time-based split (train before 2018-03, valid Mar-Apr, test May onward), baseline vs logistic vs LightGBM, early stopping on validation, SHAP and permutation importance |
| Validation | `python/05_validate.py`, `sql/05_ml_integration.sql` | 28 checks across ETL, star schema, features, model, plus the exact numbers the Power BI cards must show |
| Dashboard | `POWERBI_GUIDE.md` | 7 pages including the model results and a recommendations page |

## Results (from a full run on the provided data)

**Data:** 112,650 order items, 96,470 delivered orders with a known outcome, item revenue 13,591,643.70. Overall late rate 6.77%.

**Late delivery hurts satisfaction (tested):**
- Average review: **4.29 on time vs 2.27 late**, a gap of -2.02 stars (95% bootstrap CI -2.06 to -1.98). Mann-Whitney p < 0.001, Cliff's delta -0.64 (a large effect).
- 1-star reviews: **6.6% of on-time orders vs 53.8% of late orders**.
- By delay: 4.29 (on time), 3.29 (1-3 days late), 2.10 (4-7 days), 1.70 (8+ days).
- This is an association from observational data; late orders may also differ in other ways.

**Where it happens:**
- States with at least 500 orders and the highest late rate: MA 17.1%, CE 13.7%, RJ 11.9%, BA 11.9%. RJ is the biggest by volume (12,214 orders).
- Late rate rises with seller-to-customer distance: 4.5% in the closest fifth of orders to 9.7% in the farthest fifth. Same-state orders 4.5% late vs 8.0% cross-state.
- Spikes: Nov 2017 (12.4%), Feb 2018 (14.1%), Mar 2018 (19.0%).

**Model (held-out test months, May 2018 onward, 25,352 orders, 4.4% late):**

| Model | ROC-AUC | PR-AUC | Top 30% of orders capture |
|---|---|---|---|
| Baseline (always the training rate) | 0.500 | 0.044 | - |
| Logistic regression | 0.661 | 0.083 | 46% of late orders |
| **LightGBM** | **0.698** | **0.085** | **54% of late orders** |

- Risk bands separate real outcomes: late rate **9.5% (High), 6.5% (Medium), 2.4% (Low)**, about 4x between High and Low.
- The High band holds 28% of all late orders; its precision is 9.5% (2.2x the 4.4% base rate).
- Strongest signals: recent late rate of the customer's state, the promised delivery window (its relationship is not a straight line), the seller's recent late rate, and the customer state.

## Limitations (state these honestly)

- The model is a **moderate** predictor, not a precise one. Late delivery depends on carrier events that are unknown at purchase time.
- Predicted probabilities drift between periods (the late rate fell from 11.8% in validation to 4.4% in test), so use the **risk bands for ranking**, not the raw probabilities as literal chances.
- Predictions for train and validation months are in-sample; judge the model on the `test` rows only.
- Data covers 2016-2018 only. The first and last months have very few orders.
- The two T-SQL phases (`sql/*.sql`) were syntax-checked but the Python pipeline was tested against a SQLite stand-in for the database; run the SQL files on your SQL Server and fix any environment-specific error.

## Suggested actions (judgement, based on the findings above)

1. Proactively message customers whose orders land in the High risk band with a realistic delivery date; late orders drive most 1-star reviews.
2. Review carrier performance on routes into MA, CE, RJ and BA, where late rates are highest.
3. Favour nearby sellers (same-state fulfilment) for slow regions.
4. Add capacity or longer promised windows for peak periods (Nov, Feb-Mar in this data).
