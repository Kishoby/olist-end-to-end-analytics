# Power BI build guide (7 pages)

## 1. Load the data
Home > Get Data > SQL Server > `localhost`, database `OlistDB`, **Import** mode. Select:
`fact_order_items`, `dim_date`, `dim_customer`, `dim_product`, `dim_seller`,
`ml_late_predictions`, `ml_model_metrics`, `ml_feature_importance`, `ml_lift_table`,
`analysis_stat_tests`, and the views `vw_ml_seller_risk`, `vw_ml_band_performance`.

## 2. Relationships (single direction, many-to-one, dimension to fact)
| From (one) | To (many) |
|---|---|
| dim_date[date_key] | fact_order_items[purchase_date_key] and ml_late_predictions[purchase_date_key] |
| dim_customer[customer_id] | fact_order_items[customer_id] and ml_late_predictions[customer_id] |
| dim_product[product_id] | fact_order_items[product_id] and ml_late_predictions[primary_product_id] |
| dim_seller[seller_id] | fact_order_items[seller_id] and ml_late_predictions[primary_seller_id] |

`ml_model_metrics`, `ml_feature_importance`, `ml_lift_table`, `analysis_stat_tests` and the two views stay unrelated;
they feed cards and charts directly. Mark `dim_date` as the date table (`full_date`). Sort `month_name` by `mth`, `day_name` by `day_num`.
In Power Query (or a calculated column) add `risk_order = High 1, Medium 2, Low 3` and sort `risk_band` by it.

## 3. DAX measures
Put all measures in a `_Measures` table. Core sales and delivery measures:
```DAX
Revenue   = CALCULATE(SUM(fact_order_items[price]), fact_order_items[order_status] = "delivered")
Orders    = CALCULATE(DISTINCTCOUNT(fact_order_items[order_id]), fact_order_items[order_status] = "delivered")
Customers = CALCULATE(DISTINCTCOUNT(fact_order_items[customer_unique_id]), fact_order_items[order_status] = "delivered")
AOV       = DIVIDE([Revenue], [Orders])
Freight % = DIVIDE(SUM(fact_order_items[freight_value]), SUM(fact_order_items[item_total]))
Revenue LY  = CALCULATE([Revenue], SAMEPERIODLASTYEAR(dim_date[full_date]))
YoY %       = DIVIDE([Revenue] - [Revenue LY], [Revenue LY])
Revenue PM  = CALCULATE([Revenue], DATEADD(dim_date[full_date], -1, MONTH))
MoM %       = DIVIDE([Revenue] - [Revenue PM], [Revenue PM])
Revenue YTD = TOTALYTD([Revenue], dim_date[full_date])
Repeat Customers = COUNTROWS(FILTER(SUMMARIZE(fact_order_items, fact_order_items[customer_unique_id],
                     "o", DISTINCTCOUNT(fact_order_items[order_id])), [o] > 1))
Avg Review = AVERAGEX(SUMMARIZE(fact_order_items, fact_order_items[order_id], "rs", MAX(fact_order_items[review_score])), [rs])
Avg Delivery Days = AVERAGEX(SUMMARIZE(fact_order_items, fact_order_items[order_id], "d", MAX(fact_order_items[delivery_days])), [d])
Late Delivery % = DIVIDE(CALCULATE(DISTINCTCOUNT(fact_order_items[order_id]), fact_order_items[is_late] = 1),
                         CALCULATE(DISTINCTCOUNT(fact_order_items[order_id]), fact_order_items[is_late] IN {0, 1}))
```
Model-page measures:
```DAX
Test Orders Scored   = CALCULATE(COUNTROWS(ml_late_predictions), ml_late_predictions[data_split] = "test")
Test Late Rate       = CALCULATE(AVERAGE(ml_late_predictions[actual_late]), ml_late_predictions[data_split] = "test")
High-band Late Rate  = CALCULATE(AVERAGE(ml_late_predictions[actual_late]), ml_late_predictions[data_split] = "test", ml_late_predictions[risk_band] = "High")
Low-band Late Rate   = CALCULATE(AVERAGE(ml_late_predictions[actual_late]), ml_late_predictions[data_split] = "test", ml_late_predictions[risk_band] = "Low")
Risk Separation (x)  = DIVIDE([High-band Late Rate], [Low-band Late Rate])
Model ROC-AUC = CALCULATE(SUM(ml_model_metrics[value]), ml_model_metrics[model] = "LightGBM", ml_model_metrics[data_split] = "test", ml_model_metrics[metric] = "roc_auc")
Model PR-AUC  = CALCULATE(SUM(ml_model_metrics[value]), ml_model_metrics[model] = "LightGBM", ml_model_metrics[data_split] = "test", ml_model_metrics[metric] = "pr_auc")
Top 30% Capture = CALCULATE(SUM(ml_model_metrics[value]), ml_model_metrics[model] = "LightGBM", ml_model_metrics[data_split] = "test", ml_model_metrics[metric] = "top_30pct_capture")
```
Always filter model visuals to `data_split = "test"` (page-level filter), because train and validation scores are in-sample.

## 4. Pages
1. **Executive overview**: cards (Revenue, Orders, Customers, AOV, Late Delivery %, Avg Review), monthly revenue line with MoM %, state map.
2. **Products and categories**: Pareto (revenue columns + cumulative % line from query 4), treemap, top and bottom products.
3. **Customers**: new vs repeat, cohort retention matrix (query 2 as a view), RFM segments (query 3).
4. **Delivery and satisfaction**: late % by state and month, avg review by delay bucket, delivery timeline by state (query 8).
5. **Sellers**: ranked table, conditional formatting, drill-through to a seller detail page; add `vw_ml_seller_risk` columns.
6. **Late-delivery risk model**: cards (ROC-AUC, PR-AUC, Top 30% Capture, Risk Separation), column chart of actual late rate by risk band,
   `ml_lift_table` decile chart, SHAP bar from `ml_feature_importance` (top 10 by `mean_abs_shap`), slicers for state and month.
7. **Insights and recommendations**: 4-5 cards fed by `analysis_stat_tests` (review gap, 1-star share, p-value, effect size) and a text box with the actions from the README.

## 5. Advanced features to include
Drill-through (seller detail), tooltip page, field parameters (switch metric), bookmarks for view toggles,
a what-if parameter (price change %), row-level security by customer state, Performance Analyzer notes for the slowest visual.

## 6. Reconcile before publishing
Open `outputs/powerbi_reconciliation.csv`. With no filters, each card must match its `expected_value`
(for example Orders 96,478, Customers 93,358, Late Delivery % 6.77%, Avg Review 4.10; Revenue is about 13.22M).
If a card differs, find out why before you screenshot it.
