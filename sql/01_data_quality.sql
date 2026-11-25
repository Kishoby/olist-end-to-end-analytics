-- 01_data_quality.sql : run after python/01_etl.py has loaded the tables
USE OlistDB;
GO
-- Row counts (expected values in the comments)
SELECT 'customers' AS tbl, COUNT(*) AS n FROM customers              -- 99,441
UNION ALL SELECT 'orders',      COUNT(*) FROM orders                 -- 99,441
UNION ALL SELECT 'order_items', COUNT(*) FROM order_items            -- 112,650
UNION ALL SELECT 'payments',    COUNT(*) FROM payments               -- 103,886
UNION ALL SELECT 'reviews',     COUNT(*) FROM reviews                -- 99,224
UNION ALL SELECT 'products',    COUNT(*) FROM products               -- 32,951
UNION ALL SELECT 'sellers',     COUNT(*) FROM sellers                -- 3,095
UNION ALL SELECT 'category_translation', COUNT(*) FROM category_translation -- 73 (71 + 2 added by the ETL)
UNION ALL SELECT 'geo_zip',     COUNT(*) FROM geo_zip;               -- 19,010

-- Orphan checks (every result should be 0)
SELECT COUNT(*) AS items_without_order   FROM order_items i LEFT JOIN orders o   ON o.order_id = i.order_id     WHERE o.order_id IS NULL;
SELECT COUNT(*) AS orders_without_customer FROM orders o    LEFT JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_id IS NULL;
SELECT COUNT(*) AS items_without_product FROM order_items i LEFT JOIN products p ON p.product_id = i.product_id WHERE p.product_id IS NULL;
SELECT COUNT(*) AS items_without_seller  FROM order_items i LEFT JOIN sellers s  ON s.seller_id = i.seller_id   WHERE s.seller_id IS NULL;

-- Duplicate keys (every result should be 0)
SELECT COUNT(*) - COUNT(DISTINCT order_id)    AS dup_orders    FROM orders;
SELECT COUNT(*) - COUNT(DISTINCT customer_id) AS dup_customers FROM customers;

-- Known quirks to document in the README
SELECT COUNT(*) - COUNT(DISTINCT order_id) AS orders_with_extra_reviews FROM reviews;   -- 551
SELECT COUNT(*) - COUNT(DISTINCT order_id) AS extra_payment_rows        FROM payments;  -- 4,446
SELECT COUNT(*) AS delivered_without_delivery_date FROM orders WHERE order_status = 'delivered' AND delivered_ts IS NULL; -- 8
SELECT order_status, COUNT(*) AS n FROM orders GROUP BY order_status ORDER BY n DESC;

-- Revenue control total
SELECT SUM(price) AS item_revenue FROM order_items;  -- 13,591,643.70
