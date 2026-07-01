/* =============================================================================
   CRM Customer Intelligence Module — Bronze Layer DDL
   =============================================================================
   Database  : CRM_Analytics  (rename below if you've already named it elsewhere)
   Schema    : staging
   Purpose   : Raw, untouched, append-only landing tables for the 8 Olist CSVs.
               No constraints, no FKs, no cleaning — that is Silver's job.
               Column names/order here match FILES[...].dtypes in
               ingest_bronze.py exactly. If you rename a column in one,
               rename it in the other or the load will fail on column mismatch.

   Run order : 00_setup -> this file -> ingest_bronze.py --dry-run -> ingest_bronze.py
   Idempotent: Yes. Safe to re-run; tables are dropped and recreated, not
               altered in place. Do NOT re-run this against a Silver/Gold
               database that already has views depending on these tables
               without checking first (see DROP section below).
   ============================================================================= */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* -----------------------------------------------------------------------------
   00. Database (skip this block if the DB already exists — won't hurt to run)
   ----------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'CRM_Analytics')
BEGIN
    PRINT 'Creating database CRM_Analytics...';
    EXEC('CREATE DATABASE CRM_Analytics');
END
ELSE
BEGIN
    PRINT 'Database CRM_Analytics already exists, skipping create.';
END
GO

USE CRM_Analytics;
GO

/* -----------------------------------------------------------------------------
   01. Schema
   ----------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'staging')
BEGIN
    EXEC('CREATE SCHEMA staging');
    PRINT 'Created schema [staging]';
END
GO

/* -----------------------------------------------------------------------------
   02. Drop existing staging tables (clean rebuild)
   ----------------------------------------------------------------------------- */
-- Order doesn't matter here — staging tables carry no FKs by design,
-- so there are no constraint-dependency issues to sequence around.
DECLARE @tbl NVARCHAR(128);
DECLARE @tables TABLE (name NVARCHAR(128));
INSERT INTO @tables (name) VALUES
    ('stg_customers'), ('stg_sellers'), ('stg_products'),
    ('stg_geolocation'), ('stg_orders'), ('stg_order_items'),
    ('stg_order_payments'), ('stg_order_reviews'), ('stg_product_category_translation');

DECLARE tbl_cursor CURSOR FOR SELECT name FROM @tables;
OPEN tbl_cursor;
FETCH NEXT FROM tbl_cursor INTO @tbl;
WHILE @@FETCH_STATUS = 0
BEGIN
    IF EXISTS (
        SELECT 1 FROM sys.tables t
        JOIN sys.schemas s ON t.schema_id = s.schema_id
        WHERE s.name = 'staging' AND t.name = @tbl
    )
    BEGIN
        EXEC('DROP TABLE staging.' + @tbl);
        PRINT 'Dropped staging.' + @tbl;
    END
    FETCH NEXT FROM tbl_cursor INTO @tbl;
END
CLOSE tbl_cursor;
DEALLOCATE tbl_cursor;
GO

/* -----------------------------------------------------------------------------
   03. stg_customers
   Source: olist_customers_dataset.csv  (99,441 rows)
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_customers (
    customer_id                VARCHAR(50)     NOT NULL,
    customer_unique_id         VARCHAR(50)     NOT NULL,
    customer_zip_code_prefix   VARCHAR(10)     NULL,   -- string: preserves leading zeros
    customer_city              NVARCHAR(150)   NULL,   -- NVARCHAR: city names carry accents (São Paulo)
    customer_state             VARCHAR(5)      NULL,
    load_timestamp             DATETIME2(3)    NOT NULL,
    source_file                VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   04. stg_sellers
   Source: olist_sellers_dataset.csv  (3,095 rows)
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_sellers (
    seller_id                  VARCHAR(50)     NOT NULL,
    seller_zip_code_prefix     VARCHAR(10)     NULL,
    seller_city                NVARCHAR(150)   NULL,
    seller_state                VARCHAR(5)     NULL,
    load_timestamp             DATETIME2(3)    NOT NULL,
    source_file                VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   05. stg_products
   Source: olist_products_dataset.csv  (32,951 rows)
   NOTE: "lenght" is a misspelling in Olist's actual source CSV header.
         Kept verbatim — pandas reads the header as-is, so the staging
         column must match or the load fails on column-name mismatch.
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_products (
    product_id                     VARCHAR(50)     NOT NULL,
    product_category_name          NVARCHAR(100)   NULL,
    product_name_lenght             FLOAT          NULL,  -- sic: source spelling
    product_description_lenght      FLOAT         NULL,  -- sic: source spelling
    product_photos_qty             FLOAT           NULL,
    product_weight_g               FLOAT           NULL,
    product_length_cm              FLOAT           NULL,
    product_height_cm              FLOAT           NULL,
    product_width_cm               FLOAT           NULL,
    load_timestamp                 DATETIME2(3)    NOT NULL,
    source_file                    VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   06. stg_geolocation
   Source: olist_geolocation_dataset.csv  (~3.5M raw rows, heavy duplication
           on zip/lat/lng — see ingest_bronze.py note on expected_rows)
   No PK/index here on purpose: Bronze is append-only and rebuilt from
   scratch every load via TRUNCATE. Index this in Silver, not here.
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_geolocation (
    geolocation_zip_code_prefix    VARCHAR(10)     NULL,
    geolocation_lat                FLOAT           NULL,
    geolocation_lng                FLOAT           NULL,
    geolocation_city               NVARCHAR(150)   NULL,
    geolocation_state              VARCHAR(5)      NULL,
    load_timestamp                 DATETIME2(3)    NOT NULL,
    source_file                    VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   07. stg_orders
   Source: olist_orders_dataset.csv  (99,441 rows)
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_orders (
    order_id                        VARCHAR(50)     NOT NULL,
    customer_id                     VARCHAR(50)     NOT NULL,
    order_status                    VARCHAR(20)     NULL,
    order_purchase_timestamp        DATETIME2(3)    NULL,
    order_approved_at               DATETIME2(3)    NULL,
    order_delivered_carrier_date    DATETIME2(3)    NULL,
    order_delivered_customer_date   DATETIME2(3)    NULL,
    order_estimated_delivery_date   DATETIME2(3)    NULL,
    load_timestamp                  DATETIME2(3)    NOT NULL,
    source_file                     VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   08. stg_order_items
   Source: olist_order_items_dataset.csv  (112,650 rows)
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_order_items (
    order_id                VARCHAR(50)     NOT NULL,
    order_item_id            INT             NOT NULL,
    product_id               VARCHAR(50)     NOT NULL,
    seller_id                VARCHAR(50)     NOT NULL,
    shipping_limit_date      DATETIME2(3)    NULL,
    price                    DECIMAL(12,2)   NULL,   -- money: DECIMAL, not FLOAT (rounding)
    freight_value            DECIMAL(12,2)   NULL,
    load_timestamp           DATETIME2(3)    NOT NULL,
    source_file              VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   09. stg_order_payments
   Source: olist_order_payments_dataset.csv  (103,886 rows)
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_order_payments (
    order_id                VARCHAR(50)     NOT NULL,
    payment_sequential       INT             NOT NULL,
    payment_type             VARCHAR(30)     NULL,
    payment_installments     INT             NULL,
    payment_value            DECIMAL(12,2)   NULL,
    load_timestamp           DATETIME2(3)    NOT NULL,
    source_file              VARCHAR(100)    NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   10. stg_order_reviews
   Source: olist_order_reviews_dataset.csv  (99,224 rows)
   review_comment_title / message use NVARCHAR(MAX): free text, unbounded,
   and may contain non-Latin characters despite the dataset being PT-BR.
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_order_reviews (
    review_id                   VARCHAR(50)      NOT NULL,
    order_id                    VARCHAR(50)      NOT NULL,
    review_score                FLOAT            NULL,
    review_comment_title        NVARCHAR(200)    NULL,
    review_comment_message      NVARCHAR(MAX)    NULL,
    review_creation_date        DATETIME2(3)     NULL,
    review_answer_timestamp     DATETIME2(3)     NULL,
    load_timestamp              DATETIME2(3)     NOT NULL,
    source_file                 VARCHAR(100)     NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   10.2. stg_product_category_translation
   Source: product_category_name_translation.csv  (71 rows)
   ----------------------------------------------------------------------------- */
CREATE TABLE staging.stg_product_category_translation (
    product_category_name          NVARCHAR(100) NOT NULL,
    product_category_name_english  NVARCHAR(100) NOT NULL,
    load_timestamp                 DATETIME2(3) NOT NULL,
    source_file                    VARCHAR(100) NOT NULL
);
GO

/* -----------------------------------------------------------------------------
   11. Extended properties — lightweight in-database documentation.
   Shows up in SSMS object explorer tooltips and in sys.extended_properties
   queries. Costs nothing, helps the next engineer (or you, in 6 months).
   ----------------------------------------------------------------------------- */
EXEC sys.sp_addextendedproperty
    @name = N'MS_Description',
    @value = N'Bronze layer: raw Olist customer records, one row per customer_id. customer_unique_id is the deduplicated business key used downstream in Silver.',
    @level0type = N'SCHEMA', @level0name = N'staging',
    @level1type = N'TABLE',  @level1name = N'stg_customers';
GO

EXEC sys.sp_addextendedproperty
    @name = N'MS_Description',
    @value = N'Bronze layer: raw Olist geolocation file. Heavy row duplication by design (multiple lat/lng samples per zip prefix) — dedupe in Silver, not here.',
    @level0type = N'SCHEMA', @level0name = N'staging',
    @level1type = N'TABLE',  @level1name = N'stg_geolocation';
GO

EXEC sys.sp_addextendedproperty
    @name = N'MS_Description',
    @value = N'Bronze layer: Portuguese→English category mapping. 71 rows, used in Silver to enrich dim_product.',
    @level0type = N'SCHEMA', @level0name = N'staging',
    @level1type = N'TABLE',  @level1name = N'stg_product_category_translation';
GO

/* -----------------------------------------------------------------------------
   12. Sanity check — run this after ingest_bronze.py completes
   ----------------------------------------------------------------------------- */
SELECT
    'stg_customers'       AS table_name, COUNT(*) AS row_count FROM staging.stg_customers
UNION ALL
SELECT 'stg_sellers',          COUNT(*) FROM staging.stg_sellers
UNION ALL
SELECT 'stg_products',         COUNT(*) FROM staging.stg_products
UNION ALL
SELECT 'stg_geolocation',      COUNT(*) FROM staging.stg_geolocation
UNION ALL
SELECT 'stg_orders',           COUNT(*) FROM staging.stg_orders
UNION ALL
SELECT 'stg_order_items',      COUNT(*) FROM staging.stg_order_items
UNION ALL
SELECT 'stg_order_payments',   COUNT(*) FROM staging.stg_order_payments
UNION ALL
SELECT 'stg_order_reviews',    COUNT(*) FROM staging.stg_order_reviews
UNION ALL
SELECT 'stg_product_category_translation', COUNT(*) FROM staging.stg_product_category_translation;
GO

PRINT '=============================================================';
PRINT 'Bronze layer DDL complete. 8 staging tables created.';
PRINT 'Next: run ingest_bronze.py --dry-run, then ingest_bronze.py';
PRINT '=============================================================';
GO
