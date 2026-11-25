    -- 02_star_schema.sql : transform the clean tables into a star schema
-- Facts at order-item grain. Payments and reviews are summarised per order first so revenue is never duplicated.
USE OlistDB;
GO
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_seller;
DROP TABLE IF EXISTS fact_order_items;
GO

;WITH d AS (SELECT CAST('2016-09-01' AS date) AS dt
            UNION ALL SELECT DATEADD(day, 1, dt) FROM d WHERE dt < '2018-12-31')
SELECT YEAR(dt)*10000 + MONTH(dt)*100 + DAY(dt) AS date_key, dt AS full_date,
       YEAR(dt) AS yr, DATEPART(quarter, dt) AS qtr, MONTH(dt) AS mth, DATENAME(month, dt) AS month_name,
       DATEFROMPARTS(YEAR(dt), MONTH(dt), 1) AS month_start,
       DATENAME(weekday, dt) AS day_name, DATEPART(weekday, dt) AS day_num
INTO dim_date FROM d OPTION (MAXRECURSION 0);

SELECT customer_id, customer_unique_id, customer_zip_code_prefix AS zip_prefix,
       customer_city AS city, customer_state AS state
INTO dim_customer FROM customers;

SELECT p.product_id, COALESCE(t.category_en, p.category_pt, 'unknown') AS category,
       p.name_length, p.description_length, p.photos_qty, p.weight_g,
       p.length_cm * p.height_cm * p.width_cm AS volume_cm3
INTO dim_product FROM products p LEFT JOIN category_translation t ON t.category_pt = p.category_pt;

SELECT seller_id, seller_zip_code_prefix AS zip_prefix, seller_city AS city, seller_state AS state
INTO dim_seller FROM sellers;

;WITH pay AS (
  SELECT order_id, MAX(payment_installments) AS max_installments,
         COUNT(DISTINCT payment_type) AS payment_methods,
         MAX(CASE WHEN payment_sequential = 1 THEN payment_type END) AS primary_payment_type
  FROM payments GROUP BY order_id),
rev AS (
  SELECT order_id, review_score FROM (
    SELECT order_id, review_score,
           ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY answer_ts DESC, review_id) AS rn
    FROM reviews) x WHERE rn = 1)
SELECT i.order_id, i.order_item_id, i.product_id, i.seller_id, o.customer_id, c.customer_unique_id,
       YEAR(o.purchase_ts)*10000 + MONTH(o.purchase_ts)*100 + DAY(o.purchase_ts) AS purchase_date_key,
       o.order_status, o.purchase_ts, o.approved_ts, o.carrier_ts, o.delivered_ts, o.estimated_ts,
       i.price, i.freight_value, i.price + i.freight_value AS item_total,
       DATEDIFF(day, o.purchase_ts, o.delivered_ts)  AS delivery_days,
       DATEDIFF(day, o.estimated_ts, o.delivered_ts) AS delay_days,
       CASE WHEN o.delivered_ts IS NULL THEN NULL
            WHEN DATEDIFF(day, o.estimated_ts, o.delivered_ts) > 0 THEN 1 ELSE 0 END AS is_late,
       p.primary_payment_type, p.max_installments, p.payment_methods, r.review_score
INTO fact_order_items
FROM order_items i
JOIN orders o     ON o.order_id = i.order_id
JOIN customers c  ON c.customer_id = o.customer_id
LEFT JOIN pay p   ON p.order_id = i.order_id
LEFT JOIN rev r   ON r.order_id = i.order_id;
GO

-- Control totals: must be 112,650 rows and 13,591,643.70
SELECT COUNT(*) AS fact_rows, SUM(price) AS fact_revenue FROM fact_order_items;
