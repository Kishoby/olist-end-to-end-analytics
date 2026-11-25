-- 03_analysis_queries.sql : eight advanced analytical queries (run on the star schema)
USE OlistDB;
GO

-- 1. Monthly revenue with MoM growth, cumulative total and 3-month rolling average
--    Focus on 2017-01 .. 2018-08; the first and last months have very few orders.
WITH m AS (
  SELECT d.month_start, SUM(f.price) AS revenue, COUNT(DISTINCT f.order_id) AS orders
  FROM fact_order_items f JOIN dim_date d ON d.date_key = f.purchase_date_key
  WHERE f.order_status = 'delivered' GROUP BY d.month_start)
SELECT month_start, revenue, orders,
       revenue - LAG(revenue) OVER (ORDER BY month_start) AS mom_change,
       (revenue - LAG(revenue) OVER (ORDER BY month_start)) / NULLIF(LAG(revenue) OVER (ORDER BY month_start), 0) AS mom_growth,
       SUM(revenue) OVER (ORDER BY month_start) AS cumulative_revenue,
       AVG(revenue) OVER (ORDER BY month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling_3m_avg
FROM m ORDER BY month_start;
GO

-- 2. Cohort retention (customer_unique_id is the real person; customer_id changes on every order)
WITH o AS (
  SELECT DISTINCT customer_unique_id, order_id,
         DATEFROMPARTS(YEAR(purchase_ts), MONTH(purchase_ts), 1) AS order_month
  FROM fact_order_items WHERE order_status <> 'canceled'),
fo AS (SELECT customer_unique_id, MIN(order_month) AS cohort_month FROM o GROUP BY customer_unique_id),
c AS (
  SELECT fo.cohort_month, DATEDIFF(month, fo.cohort_month, o.order_month) AS month_index,
         COUNT(DISTINCT o.customer_unique_id) AS customers
  FROM o JOIN fo ON fo.customer_unique_id = o.customer_unique_id
  GROUP BY fo.cohort_month, DATEDIFF(month, fo.cohort_month, o.order_month))
SELECT cohort_month, month_index, customers,
       customers * 1.0 / FIRST_VALUE(customers) OVER (PARTITION BY cohort_month ORDER BY month_index) AS retention_rate
FROM c ORDER BY cohort_month, month_index;
GO

-- 3. RFM segmentation (frequency uses fixed bands because most customers order once)
WITH base AS (
  SELECT customer_unique_id,
         DATEDIFF(day, MAX(purchase_ts), (SELECT MAX(purchase_ts) FROM fact_order_items)) AS recency_days,
         COUNT(DISTINCT order_id) AS frequency, SUM(price) AS monetary
  FROM fact_order_items WHERE order_status = 'delivered' GROUP BY customer_unique_id),
scored AS (
  SELECT *, NTILE(5) OVER (ORDER BY recency_days DESC) AS r,
            NTILE(5) OVER (ORDER BY monetary) AS m,
            CASE WHEN frequency >= 3 THEN 5 WHEN frequency = 2 THEN 4 ELSE 1 END AS f
  FROM base)
SELECT *, CASE WHEN r >= 4 AND f >= 4 THEN 'Champions'
               WHEN f >= 4 THEN 'Loyal'
               WHEN r >= 4 AND m >= 4 THEN 'High-value new'
               WHEN r <= 2 AND m >= 4 THEN 'At-risk high-value'
               WHEN r <= 2 THEN 'Lapsed'
               ELSE 'Regular' END AS segment
FROM scored;
GO

-- 4. Pareto: which categories make up 80% of revenue
WITH cat AS (
  SELECT p.category, SUM(f.price) AS revenue
  FROM fact_order_items f JOIN dim_product p ON p.product_id = f.product_id
  WHERE f.order_status = 'delivered' GROUP BY p.category),
x AS (
  SELECT category, revenue, revenue * 1.0 / SUM(revenue) OVER () AS share,
         SUM(revenue) OVER (ORDER BY revenue DESC ROWS UNBOUNDED PRECEDING) * 1.0 / SUM(revenue) OVER () AS cum_share
  FROM cat)
SELECT category, revenue, share, cum_share,
       CASE WHEN cum_share - share < 0.8 THEN 'Top 80%' ELSE 'Long tail' END AS pareto_group
FROM x ORDER BY revenue DESC;
GO

-- 5. Delivery delay vs review score (one row per order)
WITH o AS (
  SELECT DISTINCT order_id, delay_days, review_score FROM fact_order_items
  WHERE delivered_ts IS NOT NULL AND review_score IS NOT NULL)
SELECT CASE WHEN delay_days <= 0 THEN '1. On time or early'
            WHEN delay_days <= 3 THEN '2. 1-3 days late'
            WHEN delay_days <= 7 THEN '3. 4-7 days late'
            ELSE '4. 8+ days late' END AS delay_bucket,
       COUNT(*) AS orders, AVG(review_score * 1.0) AS avg_review
FROM o
GROUP BY CASE WHEN delay_days <= 0 THEN '1. On time or early'
              WHEN delay_days <= 3 THEN '2. 1-3 days late'
              WHEN delay_days <= 7 THEN '3. 4-7 days late'
              ELSE '4. 8+ days late' END
ORDER BY delay_bucket;
GO

-- 6. Top 3 sellers per state (DENSE_RANK)
SELECT * FROM (
  SELECT s.state AS seller_state, f.seller_id, SUM(f.price) AS revenue,
         COUNT(DISTINCT f.order_id) AS orders, AVG(f.review_score * 1.0) AS avg_review,
         DENSE_RANK() OVER (PARTITION BY s.state ORDER BY SUM(f.price) DESC) AS rnk
  FROM fact_order_items f JOIN dim_seller s ON s.seller_id = f.seller_id
  WHERE f.order_status = 'delivered' GROUP BY s.state, f.seller_id) x
WHERE rnk <= 3 ORDER BY seller_state, rnk;
GO

-- 7. Top 3 products per category (ROW_NUMBER)
SELECT * FROM (
  SELECT p.category, f.product_id, SUM(f.price) AS revenue, COUNT(*) AS units_sold,
         ROW_NUMBER() OVER (PARTITION BY p.category ORDER BY SUM(f.price) DESC) AS rn
  FROM fact_order_items f JOIN dim_product p ON p.product_id = f.product_id
  WHERE f.order_status = 'delivered' GROUP BY p.category, f.product_id) x
WHERE rn <= 3 ORDER BY category, rn;
GO

-- 8. Order timeline by customer state: where does the time go?
SELECT c.state, COUNT(*) AS orders,
       AVG(DATEDIFF(hour, o.purchase_ts, o.approved_ts) / 24.0) AS approval_days,
       AVG(DATEDIFF(day, o.approved_ts, o.carrier_ts) * 1.0)    AS seller_handling_days,
       AVG(DATEDIFF(day, o.carrier_ts, o.delivered_ts) * 1.0)   AS transit_days,
       AVG(DATEDIFF(day, o.purchase_ts, o.delivered_ts) * 1.0)  AS total_days
FROM orders o JOIN dim_customer c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered' AND o.delivered_ts IS NOT NULL
  AND o.approved_ts IS NOT NULL AND o.carrier_ts IS NOT NULL
GROUP BY c.state ORDER BY total_days DESC;
GO
