/* =============================================================================
   mart.sp_refresh_mart
   Silver (warehouse) -> Gold (mart).

   CANONICAL CONSTANTS — declared once here, never hardcoded elsewhere:
     @as_of_date        = MAX(order_purchase_timestamp) across fact_orders,
                           computed fresh every run, not a literal. Used for
                           recency_days and days_since_last_order so RFM and
                           customer_360 can never disagree on "today".
     @ml_cutoff_date     = '2018-05-01' — the EDA-locked cutoff (Phase 2
                           report explicitly killed the original 2018-09-01
                           for leaving ~0 test rows). If this ever needs to
                           change, change it here ONCE.
     @churn_window_days  = 180 — EDA-locked churn definition.

   IDEMPOTENCY: mart tables carry no FKs to warehouse (by design — mart is
   fully disposable and rebuilt every run; integrity guarantees live in
   Silver). TRUNCATE is therefore safe everywhere here, unlike Silver's
   sp_load_warehouse where dim_product/dim_seller are FK targets.

   crm_action_queue is NEVER touched by this procedure — Python (run.py)
   owns that table completely.

   LOAD ORDER:
     1. mart.customer_360        (base — everything else reads from this)
     2. mart.rfm_features        (reads customer_360)
     3. mart.clv_features        (reads customer_360 + fact_order_items + dim_product + fact_orders)
     4. mart.sentiment_scores    (reads dim_review directly — independent of customer_360)
   ============================================================================= */
USE CRM_Analytics;
GO

CREATE OR ALTER PROCEDURE mart.sp_refresh_mart
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @as_of_date        DATE;
    DECLARE @ml_cutoff_date    DATE = '2018-05-01';
    DECLARE @churn_window_days INT  = 180;

    SELECT @as_of_date = CAST(MAX(order_purchase_timestamp) AS DATE)
    FROM warehouse.fact_orders
    WHERE order_purchase_date_sk <> 19000101;   -- exclude the sentinel row from the as-of calc

    BEGIN TRANSACTION;
    BEGIN TRY

        /* -----------------------------------------------------------------
           1. mart.customer_360
           Review aggregation is done per order_id FIRST (review_per_order
           CTE), then rolled up to customer — this is the Phase 3 standing
           rule for the multi-review-per-order Olist quirk. Skipping this
           step and joining dim_review directly to fact_order_items would
           silently fan out GMV.
           ----------------------------------------------------------------- */
        TRUNCATE TABLE mart.customer_360;

        ;WITH review_per_order AS (
            -- one row per order_id: average score across that order's reviews
            -- (handles the documented Olist quirk where an order can have >1 review row)
            SELECT
                order_id,
                customer_unique_id,
                AVG(CAST(review_score AS DECIMAL(4,2))) AS order_avg_review_score
            FROM warehouse.dim_review
            GROUP BY order_id, customer_unique_id
        ),
        order_items_agg AS (
            -- GMV and freight per order, via the denormalized customer_unique_id on fact_order_items
            SELECT
                order_id,
                customer_unique_id,
                SUM(gmv) AS order_gmv,
                SUM(freight_value) AS order_freight
            FROM warehouse.fact_order_items
            GROUP BY order_id, customer_unique_id
        ),
        customer_base AS (
            SELECT
                fo.customer_unique_id,
                COUNT(DISTINCT fo.order_id)                              AS total_orders,
                MIN(CAST(fo.order_purchase_timestamp AS DATE))            AS first_order_date,
                MAX(CAST(fo.order_purchase_timestamp AS DATE))            AS last_order_date,
                AVG(CAST(fo.delivery_delta_days AS DECIMAL(8,2)))         AS avg_delivery_delta_days,
                AVG(CASE WHEN fo.is_late = 1 THEN 1.0 ELSE 0.0 END)       AS pct_late_deliveries
            FROM warehouse.fact_orders fo
            GROUP BY fo.customer_unique_id
        ),
        customer_gmv AS (
            SELECT
                customer_unique_id,
                SUM(order_gmv) AS total_gmv,
                SUM(order_freight) AS total_freight_paid
            FROM order_items_agg
            GROUP BY customer_unique_id
        ),
        customer_reviews AS (
            SELECT
                customer_unique_id,
                AVG(order_avg_review_score) AS avg_review_score,
                AVG(CASE WHEN order_avg_review_score <= 2 THEN 1.0 ELSE 0.0 END) AS pct_negative_reviews
            FROM review_per_order
            GROUP BY customer_unique_id
        ),
        scored AS (
            SELECT
                dc.customer_unique_id,
                dc.customer_state,
                dc.customer_city,
                cb.total_orders,
                ISNULL(cg.total_gmv, 0)                                            AS total_gmv,
                ISNULL(cg.total_freight_paid, 0)                                   AS total_freight_paid,
                CASE WHEN cb.total_orders > 0
                     THEN ISNULL(cg.total_gmv, 0) / cb.total_orders
                     ELSE 0 END                                                     AS avg_order_value,
                cb.first_order_date,
                cb.last_order_date,
                DATEDIFF(DAY, cb.first_order_date, cb.last_order_date)              AS tenure_days,
                DATEDIFF(DAY, cb.last_order_date, @as_of_date)                      AS days_since_last_order,
                cr.avg_review_score,
                cr.pct_negative_reviews,
                cb.avg_delivery_delta_days,
                cb.pct_late_deliveries,
                CASE WHEN DATEDIFF(DAY, cb.last_order_date, @as_of_date) > @churn_window_days
                     THEN 1 ELSE 0 END                                              AS is_churned,
                -- percentile-rank inputs — NOT linear max-scaling (a single whale
                -- customer would collapse everyone else's monetary score toward 0;
                -- see Phase 4 plan note on Olist's documented GMV concentration)
                PERCENT_RANK() OVER (ORDER BY DATEDIFF(DAY, cb.last_order_date, @as_of_date) DESC) AS recency_pct,
                PERCENT_RANK() OVER (ORDER BY ISNULL(cg.total_gmv, 0) ASC)                          AS monetary_pct,
                -- Customers with zero reviews are imputed to the POPULATION MEDIAN
                -- avg_review_score, not 0. Defaulting to 0 would rank every silent
                -- (non-reviewing) customer as if they left the worst possible score
                -- — no feedback is not the same signal as an angry customer, and
                -- ~58.71% of orders have no review text per the Phase 2 EDA, so this
                -- isn't a rare edge case, it's a large chunk of the population.
                PERCENT_RANK() OVER (ORDER BY ISNULL(cr.avg_review_score, median_review.median_score) ASC) AS satisfaction_pct
            FROM warehouse.dim_customer dc
            JOIN customer_base cb ON cb.customer_unique_id = dc.customer_unique_id
            LEFT JOIN customer_gmv cg ON cg.customer_unique_id = dc.customer_unique_id
            LEFT JOIN customer_reviews cr ON cr.customer_unique_id = dc.customer_unique_id
            CROSS JOIN (
                SELECT TOP 1
                    CAST(
                        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_review_score) OVER ()
                    AS DECIMAL(4,2)) AS median_score
                FROM customer_reviews
            ) median_review
            WHERE dc.is_current = 1
        )
        INSERT INTO mart.customer_360 (
            customer_unique_id, customer_state, customer_city,
            total_orders, total_gmv, total_freight_paid, avg_order_value,
            first_order_date, last_order_date, tenure_days, days_since_last_order,
            avg_review_score, pct_negative_reviews,
            avg_delivery_delta_days, pct_late_deliveries,
            is_churned, customer_health_score
        )
        SELECT
            customer_unique_id, customer_state, customer_city,
            total_orders, total_gmv, total_freight_paid, avg_order_value,
            first_order_date, last_order_date, tenure_days, days_since_last_order,
            avg_review_score, pct_negative_reviews,
            avg_delivery_delta_days, pct_late_deliveries,
            is_churned,
            ROUND((recency_pct * 0.4 + monetary_pct * 0.4 + satisfaction_pct * 0.2) * 100, 1) AS customer_health_score
        FROM scored;

        PRINT 'mart.customer_360: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded. as_of_date = ' + CONVERT(VARCHAR, @as_of_date, 23);

        /* -----------------------------------------------------------------
           2. mart.rfm_features
           Same @as_of_date as customer_360 — never recomputed separately.
           ----------------------------------------------------------------- */
        TRUNCATE TABLE mart.rfm_features;

        ;WITH rfm_base AS (
            SELECT
                customer_unique_id,
                days_since_last_order AS recency_days,
                total_orders          AS frequency,
                total_gmv             AS monetary
            FROM mart.customer_360
        ),
        rfm_scored AS (
            SELECT
                customer_unique_id, recency_days, frequency, monetary,
                NTILE(5) OVER (ORDER BY recency_days DESC)   AS recency_score,    -- fewer days = higher score
                NTILE(5) OVER (ORDER BY frequency DESC)     AS frequency_score,  -- more orders = higher score
                NTILE(5) OVER (ORDER BY monetary DESC)      AS monetary_score    -- more spend = higher score
            FROM rfm_base
        )
        INSERT INTO mart.rfm_features (
            customer_unique_id, recency_days, frequency, monetary,
            recency_score, frequency_score, monetary_score, rfm_score
        )
        SELECT
            customer_unique_id, recency_days, frequency, monetary,
            recency_score, frequency_score, monetary_score,
            CAST(recency_score AS VARCHAR(1)) + CAST(frequency_score AS VARCHAR(1)) + CAST(monetary_score AS VARCHAR(1))
        FROM rfm_scored;

        PRINT 'mart.rfm_features: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded.';

        /* -----------------------------------------------------------------
           3. mart.clv_features
           preferred_payment_type tiebreak: most frequent payment_type by
           order count -> ties broken by total payment_value DESC -> final
           tiebreak alphabetical on payment_type for full determinism.
           actual_gmv_post_cutoff uses @ml_cutoff_date (2018-05-01), the
           EDA-locked value — never the dead 2018-09-01 cutoff.
           ----------------------------------------------------------------- */
        TRUNCATE TABLE mart.clv_features;

        ;WITH category_diversity AS (
            SELECT
                foi.customer_unique_id,
                COUNT(DISTINCT dp.product_category_name_english) AS total_categories_purchased
            FROM warehouse.fact_order_items foi
            LEFT JOIN warehouse.dim_product dp ON dp.product_sk = foi.product_sk
            GROUP BY foi.customer_unique_id
        ),
        payment_ranked AS (
            -- one row per (customer, payment_type): order count + total value for that combo
            SELECT
                fo.customer_unique_id,
                fo.payment_type_primary AS payment_type,
                COUNT(*)                AS order_count,
                SUM(ISNULL(fo.total_payment_value, 0)) AS total_value
            FROM warehouse.fact_orders fo
            WHERE fo.payment_type_primary IS NOT NULL
            GROUP BY fo.customer_unique_id, fo.payment_type_primary
        ),
        payment_preferred AS (
            SELECT
                customer_unique_id,
                payment_type,
                ROW_NUMBER() OVER (
                    PARTITION BY customer_unique_id
                    ORDER BY order_count DESC, total_value DESC, payment_type ASC
                ) AS rn
            FROM payment_ranked
        ),
        post_cutoff_gmv AS (
            SELECT
                foi.customer_unique_id,
                SUM(foi.gmv) AS actual_gmv_post_cutoff
            FROM warehouse.fact_order_items foi
            JOIN warehouse.fact_orders fo ON fo.order_id = foi.order_id
            WHERE fo.order_purchase_timestamp >= @ml_cutoff_date
            GROUP BY foi.customer_unique_id
        )
        INSERT INTO mart.clv_features (
            customer_unique_id, avg_order_value, order_frequency_per_month, tenure_months,
            total_categories_purchased, avg_review_score, avg_delivery_delta, pct_late,
            customer_state, days_since_last_order, preferred_payment_type, actual_gmv_post_cutoff
        )
        SELECT
            c.customer_unique_id,
            c.avg_order_value,
            CASE WHEN c.tenure_days > 0 THEN c.total_orders / (c.tenure_days / 30.0) ELSE NULL END,  -- NULLIF-style guard: same-day-only customers get NULL, not a divide-by-zero
            CASE WHEN c.tenure_days IS NOT NULL THEN c.tenure_days / 30.0 ELSE NULL END,
            ISNULL(cd.total_categories_purchased, 0),
            c.avg_review_score,
            c.avg_delivery_delta_days,
            c.pct_late_deliveries,
            c.customer_state,
            c.days_since_last_order,
            pp.payment_type,
            ISNULL(pcg.actual_gmv_post_cutoff, 0)
        FROM mart.customer_360 c
        LEFT JOIN category_diversity cd ON cd.customer_unique_id = c.customer_unique_id
        LEFT JOIN payment_preferred pp ON pp.customer_unique_id = c.customer_unique_id AND pp.rn = 1
        LEFT JOIN post_cutoff_gmv pcg ON pcg.customer_unique_id = c.customer_unique_id;

        PRINT 'mart.clv_features: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded. ml_cutoff_date = ' + CONVERT(VARCHAR, @ml_cutoff_date, 23);

        /* -----------------------------------------------------------------
           4. mart.sentiment_scores — skeleton, independent of customer_360
           ----------------------------------------------------------------- */
        TRUNCATE TABLE mart.sentiment_scores;

        INSERT INTO mart.sentiment_scores (
            review_id, order_id, customer_unique_id,
            review_score, review_comment_message, review_creation_date
        )
        SELECT
            review_id, order_id, customer_unique_id,
            review_score, review_comment_message, review_creation_date
        FROM warehouse.dim_review;

        PRINT 'mart.sentiment_scores: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded.';

        /* -----------------------------------------------------------------
           5. mart.refresh_log — write the run's canonical constants so
           views can expose "data as of" without recomputing their own
           clock (which would risk drifting from customer_360/rfm_features).
           ----------------------------------------------------------------- */
        IF EXISTS (SELECT 1 FROM mart.refresh_log WHERE refresh_id = 1)
            UPDATE mart.refresh_log
            SET as_of_date = @as_of_date,
                ml_cutoff_date = @ml_cutoff_date,
                churn_window_days = @churn_window_days,
                refreshed_at = SYSUTCDATETIME()
            WHERE refresh_id = 1;
        ELSE
            INSERT INTO mart.refresh_log (refresh_id, as_of_date, ml_cutoff_date, churn_window_days)
            VALUES (1, @as_of_date, @ml_cutoff_date, @churn_window_days);

        COMMIT TRANSACTION;
        PRINT '=============================================================';
        PRINT 'mart.sp_refresh_mart — completed successfully.';
        PRINT '=============================================================';

    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0
            ROLLBACK TRANSACTION;

        PRINT 'mart.sp_refresh_mart — FAILED. Rolled back.';
        THROW;
    END CATCH
END;
GO

PRINT 'Procedure mart.sp_refresh_mart created. Run with: EXEC mart.sp_refresh_mart;';
GO