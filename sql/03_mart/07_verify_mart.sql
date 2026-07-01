/* =============================================================================
   Gold layer verification — run after EXEC mart.sp_refresh_mart;
   Expected ballpark: customer_360 / rfm_features / clv_features should all
   match dim_customer's current row count (96,096).
   ============================================================================= */
USE CRM_Analytics;
GO

SELECT 'customer_360'      AS tbl, COUNT(*) AS row_count FROM mart.customer_360
UNION ALL SELECT 'rfm_features',    COUNT(*) FROM mart.rfm_features
UNION ALL SELECT 'clv_features',    COUNT(*) FROM mart.clv_features
UNION ALL SELECT 'sentiment_scores',COUNT(*) FROM mart.sentiment_scores
UNION ALL SELECT 'refresh_log',     COUNT(*) FROM mart.refresh_log
UNION ALL SELECT 'dim_customer (current, for comparison)', COUNT(*) FROM warehouse.dim_customer WHERE is_current = 1;
GO

-- refresh_log should always have exactly 1 row, with as_of_date matching
-- the MAX non-sentinel order_purchase_timestamp in fact_orders
SELECT
    rl.as_of_date, rl.ml_cutoff_date, rl.churn_window_days, rl.refreshed_at,
    (SELECT CAST(MAX(order_purchase_timestamp) AS DATE) FROM warehouse.fact_orders WHERE order_purchase_date_sk <> 19000101) AS expected_as_of_date
FROM mart.refresh_log rl;
-- expect as_of_date = expected_as_of_date, ml_cutoff_date = '2018-05-01', churn_window_days = 180

-- Row count parity: customer_360/rfm_features/clv_features should all equal dim_customer current count
SELECT
    (SELECT COUNT(*) FROM mart.customer_360) AS c360,
    (SELECT COUNT(*) FROM mart.rfm_features) AS rfm,
    (SELECT COUNT(*) FROM mart.clv_features) AS clv,
    (SELECT COUNT(*) FROM warehouse.dim_customer WHERE is_current = 1) AS dim_customer_current;
-- expect all 4 numbers equal

-- customer_health_score range check — catches a PERCENT_RANK miscalculation immediately
SELECT
    MIN(customer_health_score) AS min_score,
    MAX(customer_health_score) AS max_score,
    SUM(CASE WHEN customer_health_score < 0 OR customer_health_score > 100 THEN 1 ELSE 0 END) AS out_of_range_count
FROM mart.customer_360;
-- expect min/max within [0, 100], out_of_range_count = 0

-- health_tier distribution sanity — should roughly span all 3 tiers, not collapse to one
SELECT health_tier, COUNT(*) AS customer_count
FROM mart.customer_360
GROUP BY health_tier
ORDER BY health_tier;

-- RFM score range check — recency/frequency/monetary scores must be 1-5
SELECT
    MIN(recency_score) AS min_r, MAX(recency_score) AS max_r,
    MIN(frequency_score) AS min_f, MAX(frequency_score) AS max_f,
    MIN(monetary_score) AS min_m, MAX(monetary_score) AS max_m
FROM mart.rfm_features;
-- expect all mins=1, all maxes=5

-- actual_gmv_post_cutoff sanity: total should roughly correspond to the EDA's
-- locked ~7.36% test-window order share (92.64% train / 7.36% test at the
-- 2018-05-01 cutoff). Not an exact match (this is GMV-weighted, not order-
-- count-weighted) but should be in a similar ballpark — if this comes back
-- near 0% or near 100%, the @ml_cutoff_date constant has drifted.
SELECT
    SUM(actual_gmv_post_cutoff) AS post_cutoff_gmv,
    (SELECT SUM(total_gmv) FROM mart.customer_360) AS total_gmv,
    CAST(SUM(actual_gmv_post_cutoff) * 1.0 / (SELECT SUM(total_gmv) FROM mart.customer_360) * 100 AS DECIMAL(6,2)) AS pct_post_cutoff
FROM mart.clv_features;

-- preferred_payment_type should never be NULL for any customer with >=1 order
SELECT COUNT(*) AS customers_missing_payment_type
FROM mart.clv_features
WHERE preferred_payment_type IS NULL;
-- expect 0 (every order in this dataset has at least one payment row)

-- order_frequency_per_month / tenure_months should only be NULL for true
-- single-day customers (first_order_date = last_order_date)
SELECT COUNT(*) AS null_frequency_rows
FROM mart.clv_features clv
JOIN mart.customer_360 c ON c.customer_unique_id = clv.customer_unique_id
WHERE clv.order_frequency_per_month IS NULL
  AND c.first_order_date <> c.last_order_date;
-- expect 0 — a NULL here for a multi-day customer would mean the guard logic is wrong

-- GMV reconciliation: customer_360.total_gmv should equal fact_order_items.gmv summed by customer
SELECT TOP 20
    c.customer_unique_id,
    c.total_gmv AS mart_total_gmv,
    foi.item_gmv AS warehouse_total_gmv
FROM mart.customer_360 c
JOIN (
    SELECT customer_unique_id, SUM(gmv) AS item_gmv
    FROM warehouse.fact_order_items
    GROUP BY customer_unique_id
) foi ON foi.customer_unique_id = c.customer_unique_id
WHERE ABS(c.total_gmv - foi.item_gmv) > 0.01;
-- expect 0 rows back

-- total_freight_paid reconciliation, same pattern
SELECT TOP 20
    c.customer_unique_id,
    c.total_freight_paid AS mart_freight,
    foi.item_freight AS warehouse_freight
FROM mart.customer_360 c
JOIN (
    SELECT customer_unique_id, SUM(freight_value) AS item_freight
    FROM warehouse.fact_order_items
    GROUP BY customer_unique_id
) foi ON foi.customer_unique_id = c.customer_unique_id
WHERE ABS(c.total_freight_paid - foi.item_freight) > 0.01;
-- expect 0 rows back

-- sentiment_scores should match dim_review row count exactly (skeleton copy, no filtering)
SELECT
    (SELECT COUNT(*) FROM mart.sentiment_scores) AS sentiment_rows,
    (SELECT COUNT(*) FROM warehouse.dim_review) AS dim_review_rows;
-- expect equal

-- view smoke tests — confirm they execute and return rows
SELECT TOP 5 * FROM mart.vw_customer_health;
SELECT TOP 5 * FROM mart.vw_churn_signals;
SELECT * FROM mart.vw_geo_performance ORDER BY total_gmv DESC;
GO