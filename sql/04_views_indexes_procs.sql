-- 04_views_indexes_procs.sql : performance and reusable objects
USE OlistDB;
GO
-- Index experiment: run the query below, note logical reads, create the indexes, run it again, compare plans.
SET STATISTICS IO ON;
SELECT d.month_start, SUM(f.price) AS revenue
FROM fact_order_items f JOIN dim_date d ON d.date_key = f.purchase_date_key GROUP BY d.month_start;
GO
CREATE CLUSTERED INDEX cix_fact_order_items ON fact_order_items (purchase_date_key, order_id, order_item_id);
CREATE INDEX ix_fact_product  ON fact_order_items (product_id)  INCLUDE (price, order_status);
CREATE INDEX ix_fact_seller   ON fact_order_items (seller_id)   INCLUDE (price, review_score);
CREATE INDEX ix_fact_customer ON fact_order_items (customer_unique_id);
GO
SELECT d.month_start, SUM(f.price) AS revenue
FROM fact_order_items f JOIN dim_date d ON d.date_key = f.purchase_date_key GROUP BY d.month_start;
SET STATISTICS IO OFF;
GO

CREATE OR ALTER VIEW vw_sales_summary AS
SELECT d.month_start, p.category, c.state AS customer_state,
       COUNT(DISTINCT f.order_id) AS orders, SUM(f.price) AS revenue, SUM(f.freight_value) AS freight
FROM fact_order_items f
JOIN dim_date d     ON d.date_key = f.purchase_date_key
JOIN dim_product p  ON p.product_id = f.product_id
JOIN dim_customer c ON c.customer_id = f.customer_id
WHERE f.order_status = 'delivered'
GROUP BY d.month_start, p.category, c.state;
GO

CREATE OR ALTER PROCEDURE usp_sales_by_period @start date, @end date AS
BEGIN
  SET NOCOUNT ON;
  SELECT p.category, COUNT(DISTINCT f.order_id) AS orders, SUM(f.price) AS revenue,
         AVG(f.review_score * 1.0) AS avg_review
  FROM fact_order_items f JOIN dim_product p ON p.product_id = f.product_id
  WHERE f.order_status = 'delivered'
    AND f.purchase_ts >= @start AND f.purchase_ts < DATEADD(day, 1, @end)
  GROUP BY p.category ORDER BY revenue DESC;
END;
GO
EXEC usp_sales_by_period '2017-01-01', '2017-12-31';
GO
