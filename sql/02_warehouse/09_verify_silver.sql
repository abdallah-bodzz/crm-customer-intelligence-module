/* =============================================================================
   Silver layer verification — run after EXEC warehouse.sp_load_warehouse;
   Expected ballpark (from Bronze row counts in Phase 2 report):
     dim_customer current rows : ~96,096 unique customers (3.12% had >1 customer_id)
     dim_product                : 32,951
     dim_seller                  : 3,095
     dim_review                  : 99,224 (or slightly less if any score outside 1-5 was dropped)
     fact_orders                 : 99,441
     fact_order_items            : 112,650
   ============================================================================= */
USE CRM_Analytics;
GO

SELECT 'dim_customer (current)' AS tbl, COUNT(*) AS row_count FROM warehouse.dim_customer WHERE is_current = 1
UNION ALL SELECT 'dim_customer (all rows, incl. history)', COUNT(*) FROM warehouse.dim_customer
UNION ALL SELECT 'dim_product',     COUNT(*) FROM warehouse.dim_product
UNION ALL SELECT 'dim_seller',      COUNT(*) FROM warehouse.dim_seller
UNION ALL SELECT 'dim_review',      COUNT(*) FROM warehouse.dim_review
UNION ALL SELECT 'fact_orders',     COUNT(*) FROM warehouse.fact_orders
UNION ALL SELECT 'fact_order_items',COUNT(*) FROM warehouse.fact_order_items
UNION ALL SELECT 'dim_date',        COUNT(*) FROM warehouse.dim_date;
GO

-- SCD2 integrity #1: exactly one is_current=1 row per customer_unique_id
SELECT customer_unique_id, COUNT(*) AS current_row_count
FROM warehouse.dim_customer
WHERE is_current = 1
GROUP BY customer_unique_id
HAVING COUNT(*) > 1;
-- expect 0 rows back

-- SCD2 integrity #2: no overlapping [valid_from, valid_to) windows for the same customer
-- (would indicate a bug in the close-then-insert logic in sp_load_warehouse)
SELECT a.customer_unique_id, a.customer_sk AS sk_a, b.customer_sk AS sk_b
FROM warehouse.dim_customer a
JOIN warehouse.dim_customer b
    ON a.customer_unique_id = b.customer_unique_id
   AND a.customer_sk < b.customer_sk
WHERE a.valid_from < ISNULL(b.valid_to, '9999-12-31')
  AND ISNULL(a.valid_to, '9999-12-31') > b.valid_from;
-- expect 0 rows back

-- Orphan check: every fact_orders.customer_unique_id should resolve to SOME dim_customer row
SELECT COUNT(*) AS orphan_orders
FROM warehouse.fact_orders fo
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.dim_customer dc
    WHERE dc.customer_unique_id = fo.customer_unique_id
);
-- expect 0

-- Orphan check: every fact_order_items.order_id should exist in fact_orders
SELECT COUNT(*) AS orphan_items
FROM warehouse.fact_order_items fi
WHERE NOT EXISTS (
    SELECT 1 FROM warehouse.fact_orders fo WHERE fo.order_id = fi.order_id
);
-- expect 0

-- dim_review: customer_unique_id should never be null (this column is new — direct join is mandatory)
SELECT COUNT(*) AS reviews_missing_customer
FROM warehouse.dim_review
WHERE customer_unique_id IS NULL;
-- expect 0

-- Known Olist quirk, informational only: some order_ids have more than one
-- review row. Not an error in Silver — but any Gold-layer join of
-- fact_order_items/fact_orders to dim_review on order_id MUST aggregate
-- (AVG/MAX/etc.) review_score per order first, or GMV will silently
-- double-count when joined 1:many.
SELECT TOP 10 order_id, COUNT(*) AS review_count
FROM warehouse.dim_review
GROUP BY order_id
HAVING COUNT(*) > 1
ORDER BY review_count DESC;
-- if this returns rows, it's expected — just don't naive-join on order_id downstream without aggregating first

-- Unmapped products/sellers in fact_order_items (NULL FK — informational, not necessarily an error;
-- can happen if an order_item references a product_id/seller_id genuinely absent from
-- stg_products/stg_sellers in the raw Olist files)
SELECT
    SUM(CASE WHEN product_sk IS NULL THEN 1 ELSE 0 END) AS items_missing_product,
    SUM(CASE WHEN seller_sk  IS NULL THEN 1 ELSE 0 END) AS items_missing_seller
FROM warehouse.fact_order_items;

-- Payment reconciliation: fact_orders.total_payment_value should equal the
-- SUM of staging.stg_order_payments per order_id. A mismatch usually means
-- an order_id exists in payments but not in orders (or vice versa).
;WITH staging_totals AS (
    SELECT order_id, SUM(payment_value) AS staging_total
    FROM staging.stg_order_payments
    GROUP BY order_id
)
SELECT TOP 20
    fo.order_id,
    fo.total_payment_value AS warehouse_total,
    st.staging_total
FROM warehouse.fact_orders fo
JOIN staging_totals st ON st.order_id = fo.order_id
WHERE ABS(ISNULL(fo.total_payment_value, 0) - ISNULL(st.staging_total, 0)) > 0.01;
-- expect 0 rows back; if not empty, investigate the OUTER APPLY in sp_load_warehouse

-- Sentinel date check: orders with an unresolved purchase date
SELECT COUNT(*) AS orders_on_sentinel_date
FROM warehouse.fact_orders
WHERE order_purchase_date_sk = 19000101;
-- expect this to roughly match the count of NULL order_purchase_timestamp in stg_orders

-- Late delivery sanity (should roughly match Phase 2 EDA regional variance finding)
SELECT
    SUM(CASE WHEN is_late = 1 THEN 1 ELSE 0 END) AS late_orders,
    COUNT(*) AS delivered_with_dates,
    CAST(SUM(CASE WHEN is_late = 1 THEN 1.0 ELSE 0 END) / COUNT(*) * 100 AS DECIMAL(5,2)) AS pct_late
FROM warehouse.fact_orders
WHERE order_delivered_customer_date IS NOT NULL;
GO