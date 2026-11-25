-- 05_ml_integration.sql : run AFTER python/04_train_model.py has written the ml_* tables
USE OlistDB;
GO
CREATE INDEX ix_mlpred_order    ON ml_late_predictions (order_id);
CREATE INDEX ix_mlpred_seller   ON ml_late_predictions (primary_seller_id) INCLUDE (predicted_prob, risk_band, actual_late, data_split);
CREATE INDEX ix_mlpred_datekey  ON ml_late_predictions (purchase_date_key);
GO

-- How well do the risk bands separate late from on-time orders? (held-out test months only)
CREATE OR ALTER VIEW vw_ml_band_performance AS
SELECT risk_band, COUNT(*) AS orders, SUM(actual_late) AS late_orders,
       AVG(actual_late * 1.0) AS actual_late_rate, AVG(predicted_prob) AS avg_predicted_prob
FROM ml_late_predictions WHERE data_split = 'test' GROUP BY risk_band;
GO

-- Sellers to act on: many orders in the test period and the most High-risk scores
CREATE OR ALTER VIEW vw_ml_seller_risk AS
SELECT p.primary_seller_id AS seller_id, s.state AS seller_state, COUNT(*) AS orders,
       AVG(p.predicted_prob) AS avg_risk, AVG(p.actual_late * 1.0) AS actual_late_rate,
       SUM(CASE WHEN p.risk_band = 'High' THEN 1 ELSE 0 END) AS high_risk_orders
FROM ml_late_predictions p JOIN dim_seller s ON s.seller_id = p.primary_seller_id
WHERE p.data_split = 'test' GROUP BY p.primary_seller_id, s.state HAVING COUNT(*) >= 30;
GO

-- Cross-check: SQL and Python must agree on the late rate of every split
SELECT data_split, COUNT(*) AS orders, AVG(actual_late * 1.0) AS late_rate
FROM ml_late_predictions GROUP BY data_split;
SELECT 'fact_order_items' AS source, COUNT(DISTINCT order_id) AS orders, 
       AVG(CAST(is_late AS float)) AS late_rate_by_item
FROM fact_order_items WHERE is_late IS NOT NULL AND order_status = 'delivered';
GO
