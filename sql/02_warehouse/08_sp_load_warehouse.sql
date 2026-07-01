/* =============================================================================
   warehouse.sp_load_warehouse
   Bronze (staging) -> Silver (warehouse).

   IDEMPOTENCY MODEL (v2):
   - dim_customer (SCD2) is the one table that is NEVER truncated — it
     carries history by design. It is loaded incrementally (update-then-
     insert) every run.
   - Everything else (dim_product, dim_seller, dim_review, fact_orders,
     fact_order_items) is Type-1 / fact data with no independent history
     requirement, so each run clears and fully rebuilds them from the
     current Bronze snapshot. This guarantees a corrected upstream row
     (e.g. a re-ingested CSV with a fixed price) is reflected on the next
     run, instead of being silently skipped by a NOT EXISTS guard.

   CLEARING STRATEGY (matters — see the full explanation inline at step 0):
   TRUNCATE is blocked on any table that's the target of an existing FK
   constraint, full stop — order doesn't fix it, only DELETE or dropping
   the constraint does. So:
     - fact_order_items, fact_orders: TRUNCATE (nothing references them)
     - dim_review, dim_product, dim_seller: DELETE (dim_product/dim_seller
       are FK targets from fact_order_items; dim_review isn't, but is kept
       consistent with DELETE for simplicity at this row count)

   LOAD ORDER (reverse — parents before children):
     1. dim_customer  (SCD2, incremental)
     2. dim_product
     3. dim_seller
     4. dim_review
     5. fact_orders        (FK -> dim_customer @ point-in-time, dim_date)
     6. fact_order_items   (FK -> dim_product, dim_seller; order_id -> fact_orders)

   dim_date is NOT touched here — static generated calendar built once by
   01_dim_date.sql, including the 19000101 "unknown date" sentinel row.
   ============================================================================= */
USE CRM_Analytics;
GO

CREATE OR ALTER PROCEDURE warehouse.sp_load_warehouse
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @today DATE = CAST(SYSUTCDATETIME() AS DATE);

    BEGIN TRANSACTION;
    BEGIN TRY

        /* -----------------------------------------------------------------
           0. Clear Type-1 / fact tables for a clean rebuild.
           dim_customer is deliberately excluded — SCD2 history must survive.

           IMPORTANT: TRUNCATE is blocked by SQL Server whenever a FOREIGN
           KEY constraint *references* the target table — regardless of
           truncate order, and regardless of whether the referencing table
           is empty or about to be truncated itself. It checks for the
           constraint's existence, not row data. dim_product and dim_seller
           are both FK targets (from fact_order_items), so they cannot use
           TRUNCATE at all here. DELETE has no such restriction and is fine
           at this row count (33K / 3K rows — sub-second).
           fact_orders and fact_order_items are not referenced by anything,
           so TRUNCATE works for those and is kept for the (minor) speed/
           log benefit on the larger tables (~99K / ~112K rows).
           ----------------------------------------------------------------- */
        TRUNCATE TABLE warehouse.fact_order_items;
        TRUNCATE TABLE warehouse.fact_orders;
        DELETE FROM warehouse.dim_review;
        DELETE FROM warehouse.dim_product;
        DELETE FROM warehouse.dim_seller;

        PRINT 'Cleared fact_order_items, fact_orders, dim_review, dim_product, dim_seller for clean rebuild.';

        /* -----------------------------------------------------------------
           1. dim_customer — SCD Type 2 (incremental, never truncated)
           One staging row per customer_id; multiple customer_ids can map
           to the same customer_unique_id. Dedupe to one candidate row per
           customer_unique_id before comparing against the current
           warehouse row (Olist gives no "most recent" signal at Bronze,
           so the tiebreak on customer_id DESC is arbitrary but stable).
           ----------------------------------------------------------------- */

        ;WITH staging_dedup AS (
            SELECT
                s.customer_unique_id,
                s.customer_id,
                s.customer_zip_code_prefix,
                s.customer_city,
                s.customer_state,
                ROW_NUMBER() OVER (
                    PARTITION BY s.customer_unique_id
                    ORDER BY s.customer_id DESC
                ) AS rn
            FROM staging.stg_customers s
        )
        -- 1a. Close current records whose attributes changed
        UPDATE d
        SET d.valid_to   = @today,
            d.is_current = 0,
            d.updated_at = SYSUTCDATETIME()
        FROM warehouse.dim_customer d
        JOIN staging_dedup sd
            ON sd.customer_unique_id = d.customer_unique_id
           AND sd.rn = 1
        WHERE d.is_current = 1
          AND (
                ISNULL(d.customer_city, '')  <> ISNULL(sd.customer_city, '')
             OR ISNULL(d.customer_state, '') <> ISNULL(sd.customer_state, '')
             OR ISNULL(d.customer_zip_code_prefix, '') <> ISNULL(sd.customer_zip_code_prefix, '')
          );

        PRINT 'dim_customer: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' current record(s) closed (attribute change).';

        -- 1b. Insert brand-new customers + reopened versions for changed ones
        ;WITH staging_dedup AS (
            SELECT
                s.customer_unique_id,
                s.customer_id,
                s.customer_zip_code_prefix,
                s.customer_city,
                s.customer_state,
                ROW_NUMBER() OVER (
                    PARTITION BY s.customer_unique_id
                    ORDER BY s.customer_id DESC
                ) AS rn
            FROM staging.stg_customers s
        )
        INSERT INTO warehouse.dim_customer (
            customer_unique_id, customer_id,
            customer_zip_code_prefix, customer_city, customer_state,
            valid_from, valid_to, is_current
        )
        SELECT
            sd.customer_unique_id,
            sd.customer_id,
            sd.customer_zip_code_prefix,
            sd.customer_city,
            sd.customer_state,
            @today, NULL, 1
        FROM staging_dedup sd
        WHERE sd.rn = 1
          AND NOT EXISTS (
              SELECT 1 FROM warehouse.dim_customer d
              WHERE d.customer_unique_id = sd.customer_unique_id
                AND d.is_current = 1
          );

        PRINT 'dim_customer: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' new/changed record(s) inserted.';

        /* -----------------------------------------------------------------
           2. dim_product — Type 1, with category translation + UNKNOWN fallback
           Full rebuild (table was just truncated above).
           ----------------------------------------------------------------- */
        INSERT INTO warehouse.dim_product (
            product_id, product_category_name, product_category_name_english,
            product_name_length, product_description_length, product_photos_qty,
            product_weight_g, product_length_cm, product_height_cm, product_width_cm
        )
        SELECT
            p.product_id,
            p.product_category_name,
            ISNULL(t.product_category_name_english, 'UNKNOWN'),
            TRY_CAST(p.product_name_lenght AS INT),
            TRY_CAST(p.product_description_lenght AS INT),
            TRY_CAST(p.product_photos_qty AS INT),
            p.product_weight_g, p.product_length_cm, p.product_height_cm, p.product_width_cm
        FROM staging.stg_products p
        LEFT JOIN staging.stg_product_category_translation t
            ON p.product_category_name = t.product_category_name;

        PRINT 'dim_product: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded.';

        /* -----------------------------------------------------------------
           3. dim_seller — Type 1, full rebuild
           ----------------------------------------------------------------- */
        INSERT INTO warehouse.dim_seller (
            seller_id, seller_zip_code_prefix, seller_city, seller_state
        )
        SELECT s.seller_id, s.seller_zip_code_prefix, s.seller_city, s.seller_state
        FROM staging.stg_sellers s;

        PRINT 'dim_seller: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded.';

        /* -----------------------------------------------------------------
           4. dim_review — full rebuild, customer_unique_id resolved via
           stg_orders -> stg_customers so sentiment rollups skip the
           fact_orders hop entirely.
           review_score is FLOAT in Bronze (source quirk) — cast to TINYINT.

           KNOWN OLIST DATA QUIRK: review_id is NOT globally unique in the
           raw CSV — the same review_id can appear against more than one
           order_id (documented dataset oddity, not an ingestion bug).
           Since dim_review's business key is review_id, we dedupe with a
           deterministic tiebreak (latest review_creation_date, then
           order_id ASC for full determinism) and log how many rows were
           collapsed so the drop is visible, not silent.
           ----------------------------------------------------------------- */
        ;WITH review_dedup AS (
            SELECT
                r.review_id, r.order_id, sc.customer_unique_id,
                CAST(r.review_score AS TINYINT) AS review_score,
                NULLIF(LTRIM(RTRIM(r.review_comment_title)), '')   AS review_comment_title,
                NULLIF(LTRIM(RTRIM(r.review_comment_message)), '') AS review_comment_message,
                r.review_creation_date, r.review_answer_timestamp,
                ROW_NUMBER() OVER (
                    PARTITION BY r.review_id
                    ORDER BY r.review_creation_date DESC, r.order_id ASC
                ) AS rn
            FROM staging.stg_order_reviews r
            JOIN staging.stg_orders o     ON r.order_id = o.order_id
            JOIN staging.stg_customers sc ON o.customer_id = sc.customer_id
            WHERE r.review_score BETWEEN 1 AND 5   -- defensive: drop out-of-range score rather than violate the CHECK and abort the whole load
        )
        INSERT INTO warehouse.dim_review (
            review_id, order_id, customer_unique_id, review_score,
            review_comment_title, review_comment_message,
            review_creation_date, review_answer_timestamp
        )
        SELECT
            review_id, order_id, customer_unique_id, review_score,
            review_comment_title, review_comment_message,
            review_creation_date, review_answer_timestamp
        FROM review_dedup
        WHERE rn = 1;

        DECLARE @review_dupes_dropped INT = (
            SELECT COUNT(*) - COUNT(DISTINCT review_id) FROM staging.stg_order_reviews
        );
        PRINT 'dim_review: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded ('
            + CAST(@review_dupes_dropped AS VARCHAR) + ' duplicate review_id row(s) collapsed per known Olist quirk).';

        /* -----------------------------------------------------------------
           5. fact_orders — full rebuild.

           Point-in-time SCD2 join, with an honest caveat documented:
           `valid_from`/`valid_to` reflect *when the warehouse learned about
           the change* (ETL run date), not when the customer actually moved
           in the real world — Olist's Bronze data carries no effective-date
           for an address change. So:
             - For an order whose purchase date falls inside a CLOSED
               version's [valid_from, valid_to) window, that closed version
               is correct: it was the address on file at that time.
             - For an order whose purchase date predates the customer's
               very first dim_customer valid_from (true for ~100% of rows
               on a first load, since valid_from = today for everyone),
               there is no historical version to match — use the OLDEST
               known version for that customer_unique_id instead of
               is_current, since the oldest record is the closest proxy to
               "what we'd have seen if we'd run this on day one."
           Net effect: identical to a plain is_current join until the
           database has actually been re-run after a real address change;
           from that point on, historical orders stay pinned to the
           address version that was valid_from <= order_date < valid_to.
           Payment aggregation via OUTER APPLY.
           ----------------------------------------------------------------- */
        INSERT INTO warehouse.fact_orders (
            customer_sk, order_purchase_date_sk,
            order_id, customer_unique_id, customer_id,
            order_status,
            order_purchase_timestamp, order_approved_at,
            order_delivered_carrier_date, order_delivered_customer_date,
            order_estimated_delivery_date,
            total_payment_value, payment_type_primary,
            payment_installments_max, payment_methods_count
        )
        SELECT
            best.customer_sk,
            COALESCE(
                CONVERT(INT, CONVERT(NVARCHAR(8), o.order_purchase_timestamp, 112)),
                19000101
            ),
            o.order_id,
            sc.customer_unique_id,
            o.customer_id,
            o.order_status,
            o.order_purchase_timestamp,
            o.order_approved_at,
            o.order_delivered_carrier_date,
            o.order_delivered_customer_date,
            o.order_estimated_delivery_date,
            pay.total_payment_value,
            pay.payment_type_primary,
            pay.payment_installments_max,
            pay.payment_methods_count
        FROM staging.stg_orders o
        JOIN staging.stg_customers sc
            ON o.customer_id = sc.customer_id
        CROSS APPLY (
            -- Single ranked pick per order: rows whose [valid_from, valid_to)
            -- window actually covers the order date rank first (priority 0);
            -- if none covers it, fall back to the customer's earliest known
            -- version (priority 1) instead of leaving the order unmatched.
            SELECT TOP 1 dc.customer_sk
            FROM warehouse.dim_customer dc
            WHERE dc.customer_unique_id = sc.customer_unique_id
            ORDER BY
                CASE
                    WHEN o.order_purchase_timestamp IS NOT NULL
                     AND CAST(o.order_purchase_timestamp AS DATE) >= dc.valid_from
                     AND (dc.valid_to IS NULL OR CAST(o.order_purchase_timestamp AS DATE) < dc.valid_to)
                    THEN 0
                    ELSE 1
                END ASC,
                dc.valid_from ASC
        ) best
        OUTER APPLY (
            SELECT
                SUM(p2.payment_value)                                            AS total_payment_value,
                MAX(p2.payment_installments)                                     AS payment_installments_max,
                COUNT(DISTINCT p2.payment_type)                                  AS payment_methods_count,
                (SELECT TOP 1 p3.payment_type
                 FROM staging.stg_order_payments p3
                 WHERE p3.order_id = o.order_id
                 ORDER BY p3.payment_sequential ASC)                            AS payment_type_primary
            FROM staging.stg_order_payments p2
            WHERE p2.order_id = o.order_id
        ) pay;

        PRINT 'fact_orders: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded.';

        -- Safety net: any order that matched zero dim_customer rows (e.g. a customer present
        -- in stg_orders but never resolved to a current/point-in-time dim_customer row at all —
        -- shouldn't happen given dim_customer is loaded first, but cheap to confirm) is surfaced
        -- here rather than silently vanishing from fact_orders.
        IF EXISTS (
            SELECT 1 FROM staging.stg_orders o
            JOIN staging.stg_customers sc ON o.customer_id = sc.customer_id
            WHERE NOT EXISTS (
                SELECT 1 FROM warehouse.fact_orders fo WHERE fo.order_id = o.order_id
            )
        )
        BEGIN
            DECLARE @unmatched INT = (
                SELECT COUNT(*) FROM staging.stg_orders o
                JOIN staging.stg_customers sc ON o.customer_id = sc.customer_id
                WHERE NOT EXISTS (SELECT 1 FROM warehouse.fact_orders fo WHERE fo.order_id = o.order_id)
            );
            PRINT 'WARNING: ' + CAST(@unmatched AS VARCHAR) + ' staging order(s) did not resolve to a dim_customer row and were dropped from fact_orders.';
        END

        /* -----------------------------------------------------------------
           6. fact_order_items — full rebuild
           ----------------------------------------------------------------- */
        INSERT INTO warehouse.fact_order_items (
            order_id, order_item_id, customer_unique_id,
            product_sk, seller_sk, product_id, seller_id,
            price, freight_value, shipping_limit_date
        )
        SELECT
            i.order_id, i.order_item_id, fo.customer_unique_id,
            dp.product_sk, ds.seller_sk, i.product_id, i.seller_id,
            i.price, i.freight_value, i.shipping_limit_date
        FROM staging.stg_order_items i
        JOIN warehouse.fact_orders fo
            ON fo.order_id = i.order_id
        LEFT JOIN warehouse.dim_product dp ON dp.product_id = i.product_id
        LEFT JOIN warehouse.dim_seller  ds ON ds.seller_id  = i.seller_id;

        PRINT 'fact_order_items: ' + CAST(@@ROWCOUNT AS VARCHAR) + ' row(s) loaded.';

        COMMIT TRANSACTION;
        PRINT '=============================================================';
        PRINT 'warehouse.sp_load_warehouse — completed successfully.';
        PRINT '=============================================================';

    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0
            ROLLBACK TRANSACTION;

        PRINT 'warehouse.sp_load_warehouse — FAILED. Rolled back.';
        THROW;
    END CATCH
END;
GO

PRINT 'Procedure warehouse.sp_load_warehouse created. Run with: EXEC warehouse.sp_load_warehouse;';
GO