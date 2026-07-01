# Data Dictionary — Enterprise CRM Intelligence & Customer 360 Platform

**Version:** 1.0  
**Lead Developer:** Abdallah A Khames  
**Organisation:** [BODZZ](https://github.com/abdallah-bodzz)  
**Repository:** [crm-customer-intelligence-module](https://github.com/abdallah-bodzz/crm-customer-intelligence-module)  
**Database:** `CRM_Analytics`  
**Architecture:** Medallion Lakehouse (Bronze → Silver → Gold) with Customer Master Data Management (MDM)  
**Last Updated:** July 2026

---

## Table of Contents

1. [Overview & Enterprise Architecture](#1-overview--enterprise-architecture)
2. [Bronze Layer — Raw Data Ingestion](#2-bronze-layer--raw-data-ingestion)
3. [Silver Layer — Enterprise Data Warehouse & Customer MDM](#3-silver-layer--enterprise-data-warehouse--customer-mdm)
4. [Gold Layer — CRM Mart & Operational Analytics](#4-gold-layer--crm-mart--operational-analytics)
5. [Analytical Views — BI Consumption Layer](#5-analytical-views--bi-consumption-layer)
6. [Business Definitions, Metrics & Formulas](#6-business-definitions-metrics--formulas)
7. [Conceptual Data Model & Relationships](#7-conceptual-data-model--relationships)
8. [Power BI & Stakeholder Usage Guide](#8-power-bi--stakeholder-usage-guide)

---

## 1. Overview & Enterprise Architecture

This data dictionary documents the **production-grade Enterprise CRM Intelligence Platform** built on a Medallion Lakehouse architecture. The platform implements **Customer Master Data Management (MDM)** using SCD Type 2, predictive customer intelligence (Churn, CLV, Next-Purchase Timing, RFM Segmentation, PT-BR Sentiment), and an operational **CRM Action Engine** — aligned with patterns found in SAP CRM, Odoo CRM, and enterprise RevOps platforms.

### 1.1 Medallion Layer Summary

| Layer | Schema | Primary Use | Refresh Pattern |
|-------|--------|-------------|-----------------|
| **Bronze** | `staging` | Raw, unmodified ingestion from CSV. Audit trail — never modified post-load. | Append / truncate-reload per run |
| **Silver** | `warehouse` | Cleansed, typed, star-schema data warehouse. Single source of truth for business entities. Customer MDM via SCD Type 2. | Full rebuild via `sp_load_warehouse` |
| **Gold** | `mart` | Aggregated, ML-enriched, BI-ready CRM tables. Operational action queue. ML feature matrices. | Full rebuild via `sp_refresh_mart` |

### 1.2 Enterprise Alignment

| Capability | Implementation |
|-----------|----------------|
| **Customer Master Data Management (MDM)** | SCD Type 2 on `dim_customer` — `customer_unique_id` as business key, database-enforced current-row integrity via filtered unique index |
| **Customer 360° View** | `mart.customer_360` — unified account record across orders, GMV, delivery, sentiment, churn probability, CLV, and next-purchase estimate |
| **Operational CRM Action Engine** | `mart.crm_action_queue` — ML predictions translated into prioritised `RETENTION_CAMPAIGN / REACTIVATION / MONITOR` action records with human-readable trigger reasons |
| **Predictive Customer Intelligence** | Five ML models (XGBoost, K-means, Weibull AFT, LeIA) writing scored predictions back to mart tables |
| **Retention & Revenue Operations (RevOps)** | 11,957 HIGH-priority retention candidates identified; ~R$2.72M projected 6-month revenue uplift |
| **Stakeholder-Facing Analytics Platform** | 7-page Power BI report consuming Gold views — purpose-built for executive, retention, territory, and CX audiences |

### 1.3 Naming Conventions

| Convention | Pattern | Example |
|------------|---------|---------|
| Surrogate keys | `<entity>_sk` | `customer_sk`, `order_sk` |
| Business keys | `<entity>_id` or `<entity>_unique_id` | `customer_unique_id`, `order_id` |
| Computed / persisted | Noted as **Computed** in Nullable column | `health_tier`, `gmv`, `is_late` |
| Python-populated | Noted as **Filled by `<script>.py`** | `churn_probability`, `rfm_segment` |
| Audit columns | `created_at`, `refreshed_at`, `load_timestamp`, `source_file` | Consistent across all layers |

---

## 2. Bronze Layer — Raw Data Ingestion

All 9 staging tables are loaded by `python/ingest_bronze.py`. Every table carries two audit columns:

- `load_timestamp DATETIME2(3)` — UTC timestamp of when the row was ingested.
- `source_file VARCHAR(100)` — name of the originating CSV file.

All columns are `NVARCHAR` or `FLOAT` — no business-type casting at this layer. **No constraints, no transforms, no business logic.** This is the immutable audit trail.

---

### 2.1. `staging.stg_customers`

**Source:** `olist_customers_dataset.csv` · **Rows:** 99,441  
**MDM Note:** `customer_unique_id` is the real-person identifier. `customer_id` is a per-order operational key — one person can have many. Identity resolution to `customer_unique_id` happens in Silver (`dim_customer` SCD Type 2).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_id` | VARCHAR(50) | NOT NULL | Per-order operational key. Not a stable customer identifier. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | **MDM business key.** Stable real-person identifier used throughout Silver and Gold. |
| `customer_zip_code_prefix` | VARCHAR(10) | NULL | First part of Brazilian ZIP (stored as VARCHAR to preserve leading zeros). |
| `customer_city` | NVARCHAR(150) | NULL | City (PT-BR, may contain accents). |
| `customer_state` | VARCHAR(5) | NULL | Two-letter Brazilian state code. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_customers_dataset.csv` |

---

### 2.2. `staging.stg_sellers`

**Source:** `olist_sellers_dataset.csv` · **Rows:** 3,095

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `seller_id` | VARCHAR(50) | NOT NULL | Seller business key. |
| `seller_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix. |
| `seller_city` | NVARCHAR(150) | NULL | Seller city. |
| `seller_state` | VARCHAR(5) | NULL | Seller state. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_sellers_dataset.csv` |

---

### 2.3. `staging.stg_products`

**Source:** `olist_products_dataset.csv` · **Rows:** 32,951  
**Note:** Olist source CSV misspells `product_name_lenght` and `product_description_lenght` — preserved as-is at Bronze; corrected spelling in Silver (`dim_product`).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `product_id` | VARCHAR(50) | NOT NULL | Product business key. |
| `product_category_name` | NVARCHAR(100) | NULL | Portuguese category name. |
| `product_name_lenght` | FLOAT | NULL | Name length in characters (misspelled column preserved from source). |
| `product_description_lenght` | FLOAT | NULL | Description length (misspelled). |
| `product_photos_qty` | FLOAT | NULL | Number of product photos. |
| `product_weight_g` | FLOAT | NULL | Weight in grams. |
| `product_length_cm` | FLOAT | NULL | Length in cm. |
| `product_height_cm` | FLOAT | NULL | Height in cm. |
| `product_width_cm` | FLOAT | NULL | Width in cm. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_products_dataset.csv` |

---

### 2.4. `staging.stg_geolocation`

**Source:** `olist_geolocation_dataset.csv` · **Rows:** ~1M+  
**Note:** Multiple lat/lng rows per ZIP prefix — expected. Silver resolves to per-ZIP centroids for customer geo enrichment.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `geolocation_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix (join key to customer and seller tables). |
| `geolocation_lat` | FLOAT | NULL | Latitude. |
| `geolocation_lng` | FLOAT | NULL | Longitude. |
| `geolocation_city` | NVARCHAR(150) | NULL | City. |
| `geolocation_state` | VARCHAR(5) | NULL | State. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_geolocation_dataset.csv` |

---

### 2.5. `staging.stg_orders`

**Source:** `olist_orders_dataset.csv` · **Rows:** 99,441  
**Note:** Delivery date nulls (~2.98%) are expected — cancelled or undelivered orders. Handled via `1900-01-01` sentinel in Silver's `dim_date`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_id` | VARCHAR(50) | NOT NULL | Order business key. |
| `customer_id` | VARCHAR(50) | NOT NULL | Operational customer key (per-order; maps N:1 to `customer_unique_id`). |
| `order_status` | VARCHAR(20) | NULL | `delivered`, `shipped`, `canceled`, `invoiced`, etc. |
| `order_purchase_timestamp` | DATETIME2(3) | NULL | Purchase datetime. |
| `order_approved_at` | DATETIME2(3) | NULL | Payment approval datetime. |
| `order_delivered_carrier_date` | DATETIME2(3) | NULL | Handover to carrier. |
| `order_delivered_customer_date` | DATETIME2(3) | NULL | Actual customer delivery. |
| `order_estimated_delivery_date` | DATETIME2(3) | NULL | Estimated delivery (basis for delivery delta). |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_orders_dataset.csv` |

---

### 2.6. `staging.stg_order_items`

**Source:** `olist_order_items_dataset.csv` · **Rows:** 112,650

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_id` | VARCHAR(50) | NOT NULL | Order business key. |
| `order_item_id` | INT | NOT NULL | Line item sequence within the order. |
| `product_id` | VARCHAR(50) | NOT NULL | Product business key. |
| `seller_id` | VARCHAR(50) | NOT NULL | Seller business key. |
| `shipping_limit_date` | DATETIME2(3) | NULL | Seller shipping deadline. |
| `price` | DECIMAL(12,2) | NULL | Item price (BRL). |
| `freight_value` | DECIMAL(12,2) | NULL | Shipping cost for this item (BRL). |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_order_items_dataset.csv` |

---

### 2.7. `staging.stg_order_payments`

**Source:** `olist_order_payments_dataset.csv` · **Rows:** 103,886  
**Note:** One order can have multiple payment rows (split payments, installments). Aggregated into `fact_orders` in Silver via `OUTER APPLY`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_id` | VARCHAR(50) | NOT NULL | Order business key. |
| `payment_sequential` | INT | NOT NULL | Payment sequence within the order (primary method = 1). |
| `payment_type` | VARCHAR(30) | NULL | `credit_card`, `boleto`, `debit_card`, `voucher`. |
| `payment_installments` | INT | NULL | Number of installments chosen. |
| `payment_value` | DECIMAL(12,2) | NULL | Payment amount for this row (BRL). |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_order_payments_dataset.csv` |

---

### 2.8. `staging.stg_order_reviews`

**Source:** `olist_order_reviews_dataset.csv` · **Rows:** 99,224  
**Data quality note:** `review_id` is not globally unique in source — the same `review_id` can appear against multiple `order_id` values. Deduplicated in Silver (`dim_review`) via `ROW_NUMBER()` on latest `review_creation_date + order_id ASC`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `review_id` | VARCHAR(50) | NOT NULL | Review identifier (not globally unique in source). |
| `order_id` | VARCHAR(50) | NOT NULL | Order business key. |
| `review_score` | FLOAT | NULL | Score 1–5 (arrives as FLOAT; cast to TINYINT in Silver). |
| `review_comment_title` | NVARCHAR(200) | NULL | Review title (Portuguese). |
| `review_comment_message` | NVARCHAR(MAX) | NULL | Review body — input to LeIA PT-BR sentiment analysis. 58.71% NULL (no comment left). |
| `review_creation_date` | DATETIME2(3) | NULL | Review creation date. |
| `review_answer_timestamp` | DATETIME2(3) | NULL | Date seller responded. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_order_reviews_dataset.csv` |

---

### 2.9. `staging.stg_product_category_translation`

**Source:** `product_category_name_translation.csv` · **Rows:** 71

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `product_category_name` | NVARCHAR(100) | NOT NULL | Portuguese category name (join key to `stg_products`). |
| `product_category_name_english` | NVARCHAR(100) | NOT NULL | English translation (used in `dim_product` and CLV feature engineering). |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp (UTC). |
| `source_file` | VARCHAR(100) | NOT NULL | `product_category_name_translation.csv` |

---

## 3. Silver Layer — Enterprise Data Warehouse & Customer MDM

The Silver layer is the **single source of truth** for all business entities. Built and maintained by `sp_load_warehouse` — idempotent, transactional, and fully documented.

Key data engineering patterns in this layer:
- **SCD Type 2** on `dim_customer` — the core MDM capability
- **Point-in-time fact joins** via priority-ranked `CROSS APPLY` — ensures historical orders are linked to the customer address version valid at order time
- **Persisted computed columns** — `delivery_delta_days`, `is_late`, `gmv`, `freight_ratio` stored physically; never recomputed on query
- **Kimball `1900-01-01` sentinel** in `dim_date` — routes NULL dates instead of allowing NULL FKs
- **Deterministic deduplication** throughout — `review_id`, `customer_id`, `preferred_payment_type` all resolved via explicit tiebreak logic

---

### 3.1. `warehouse.dim_date`

**Primary use:** Time intelligence across all fact tables. Brazilian business calendar.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `date_sk` | INT | NOT NULL | Surrogate key in YYYYMMDD format (e.g., `20180301`). |
| `full_date` | DATE | NOT NULL | Calendar date. |
| `year` | SMALLINT | NOT NULL | Calendar year. |
| `quarter` | TINYINT | NOT NULL | Quarter (1–4). |
| `month` | TINYINT | NOT NULL | Month (1–12). |
| `month_name` | NVARCHAR(12) | NOT NULL | Month name. |
| `week_of_year` | TINYINT | NOT NULL | ISO week number. |
| `day_of_month` | TINYINT | NOT NULL | Day of month. |
| `day_name` | NVARCHAR(12) | NOT NULL | Day name. |
| `is_weekend` | BIT | NOT NULL | 1 if Saturday or Sunday. |
| `is_brazilian_holiday` | BIT | NOT NULL | 1 for fixed Brazilian public holidays (New Year, Tiradentes, Labour Day, Independence Day, etc.). |
| `holiday_name` | NVARCHAR(100) | NULL | Holiday name if applicable; NULL otherwise. |
| `fiscal_year` | SMALLINT | NOT NULL | Aligns with calendar year (Jan–Dec). |
| `fiscal_quarter` | TINYINT | NOT NULL | Aligns with calendar quarter. |

**PK:** `date_sk`  
**Range:** `2015-01-01` to `2020-12-31` + sentinel row `1900-01-01` (Kimball pattern for unknown dates).

---

### 3.2. `warehouse.dim_customer` — Customer MDM (SCD Type 2)

**Primary use:** Customer Master Data Management. The MDM backbone of the platform.  
**MDM pattern:** SCD Type 2 tracks attribute changes (city, state, ZIP) with full version history. A filtered unique index (`WHERE is_current = 1`) enforces exactly one current row per `customer_unique_id` at the **database level** — uniqueness is a schema constraint, not an ETL discipline.

**The identity problem this solves:** Olist issues a fresh `customer_id` per order. A naive `COUNT(DISTINCT customer_id)` returns 99,441. The correct unified customer count is **96,096** — a 3.36% overcount from 2,997 customers with multiple IDs (up to 17 per individual). This table resolves that.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_sk` | INT | NOT NULL | Surrogate key (identity, auto-increment). |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | **MDM business key.** Stable real-person identifier. Consistent across all Gold tables. |
| `customer_id` | VARCHAR(50) | NOT NULL | Most recent operational `customer_id` for this SCD2 version. |
| `customer_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix at time of this version. |
| `customer_city` | NVARCHAR(150) | NULL | City at time of this version. |
| `customer_state` | VARCHAR(5) | NULL | State at time of this version. |
| `valid_from` | DATE | NOT NULL | Version start date (inclusive). |
| `valid_to` | DATE | NULL | Version end date (exclusive); NULL = currently active version. |
| `is_current` | BIT | NOT NULL | 1 for the active version; 0 for closed historical versions. |
| `created_at` | DATETIME2(3) | NOT NULL | Row insert timestamp. |
| `updated_at` | DATETIME2(3) | NOT NULL | Last modification timestamp. |

**PK:** `customer_sk`  
**MDM constraint:** Filtered unique index `ux_dim_customer_current` on `customer_unique_id WHERE is_current = 1` — enforces exactly one active version per business key.  
**Indexes:** on `customer_unique_id` (include `is_current`), `customer_id`, `customer_state`.

---

### 3.3. `warehouse.dim_seller`

**Primary use:** Seller master reference for line-item attribution and regional seller analysis.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `seller_sk` | INT | NOT NULL | Surrogate key. |
| `seller_id` | VARCHAR(50) | NOT NULL | Seller business key. |
| `seller_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix. |
| `seller_city` | NVARCHAR(150) | NULL | City. |
| `seller_state` | VARCHAR(5) | NULL | State. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `seller_sk` · **Unique:** `uq_dim_seller_id` on `seller_id`.

---

### 3.4. `warehouse.dim_product`

**Primary use:** Product catalogue reference with English category translation for CLV feature engineering and category-level reporting.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `product_sk` | INT | NOT NULL | Surrogate key. |
| `product_id` | VARCHAR(50) | NOT NULL | Product business key. |
| `product_category_name` | NVARCHAR(100) | NULL | Original Portuguese category. |
| `product_category_name_english` | NVARCHAR(100) | NOT NULL | English translation; `'UNKNOWN'` for nulls (1.85% of products). |
| `product_name_length` | INT | NULL | Name length in characters (spelling corrected from Bronze). |
| `product_description_length` | INT | NULL | Description length. |
| `product_photos_qty` | INT | NULL | Number of product photos. |
| `product_weight_g` | FLOAT | NULL | Weight in grams. |
| `product_length_cm` | FLOAT | NULL | Length in cm. |
| `product_height_cm` | FLOAT | NULL | Height in cm. |
| `product_width_cm` | FLOAT | NULL | Width in cm. |
| `product_volume_cm3` | FLOAT | **Computed** | `length × height × width`; NULL if any dimension is NULL. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `product_sk` · **Unique:** `uq_dim_product_id` on `product_id`.

---

### 3.5. `warehouse.dim_review`

**Primary use:** Customer satisfaction record — source table for LeIA PT-BR sentiment analysis. One row per deduplicated review.  
**Data quality note:** Olist source has duplicate `review_id` values (same ID, different `order_id`). Silver deduplicates via `ROW_NUMBER() OVER (PARTITION BY review_id ORDER BY review_creation_date DESC, order_id ASC)`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `review_sk` | INT | NOT NULL | Surrogate key. |
| `review_id` | VARCHAR(50) | NOT NULL | Review identifier (globally unique after deduplication). |
| `order_id` | VARCHAR(50) | NOT NULL | Associated order. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Denormalised MDM key for fast customer-level aggregation without join to fact tables. |
| `review_score` | TINYINT | NOT NULL | Satisfaction score 1–5 (cast from FLOAT; invalid values excluded). |
| `review_comment_title` | NVARCHAR(200) | NULL | Review title (Portuguese). |
| `review_comment_message` | NVARCHAR(MAX) | NULL | Review body — primary input to `sentiment.py` (LeIA). NULL for ~58.71% of reviews. |
| `has_comment` | BIT | **Computed** | 1 if `review_comment_message` is non-empty; drives sentiment coverage reporting. |
| `review_creation_date` | DATETIME2(3) | NULL | Date review was written. |
| `review_answer_timestamp` | DATETIME2(3) | NULL | Date seller responded to review. |
| `response_delay_days` | INT | **Computed** | `DATEDIFF(DAY, review_creation_date, review_answer_timestamp)`. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `review_sk` · **Unique:** `uq_dim_review_id` on `review_id` · **Check:** `review_score BETWEEN 1 AND 5`.

---

### 3.6. `warehouse.fact_orders`

**Primary use:** Core transactional fact table. One row per order. Delivery performance KPIs and payment aggregates computed here via persisted columns and `OUTER APPLY`.  
**Join pattern:** Links to `dim_customer` via a priority-ranked `CROSS APPLY` (point-in-time SCD2 join) — matches the customer version whose `[valid_from, valid_to)` window covers the order date, with a fallback to the earliest known version for fresh loads.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_sk` | INT | NOT NULL | Surrogate key. |
| `customer_sk` | INT | NOT NULL | FK to `dim_customer` (point-in-time version). |
| `order_purchase_date_sk` | INT | NOT NULL | FK to `dim_date`. Routes to `19000101` sentinel if purchase date is NULL. |
| `order_id` | VARCHAR(50) | NOT NULL | Order business key. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Denormalised MDM key for fast customer rollups. |
| `customer_id` | VARCHAR(50) | NOT NULL | Operational customer key at time of order. |
| `order_status` | VARCHAR(20) | NULL | Order status. |
| `is_delivered` | BIT | **Computed** | 1 if `order_status = 'delivered'`. |
| `is_canceled` | BIT | **Computed** | 1 if `order_status = 'canceled'`. |
| `order_purchase_timestamp` | DATETIME2(3) | NULL | Purchase timestamp (full precision). |
| `order_approved_at` | DATETIME2(3) | NULL | Payment approval timestamp. |
| `order_delivered_carrier_date` | DATETIME2(3) | NULL | Carrier handover timestamp. |
| `order_delivered_customer_date` | DATETIME2(3) | NULL | Actual customer delivery timestamp. |
| `order_estimated_delivery_date` | DATETIME2(3) | NULL | Seller-committed estimated delivery date. |
| `delivery_delta_days` | INT | **Computed** | `DATEDIFF(DAY, estimated, actual)`. Negative = early; positive = late. Key churn signal. |
| `is_late` | BIT | **Computed** | 1 if actual delivery > estimated delivery. |
| `approval_delay_hours` | INT | **Computed** | `DATEDIFF(HOUR, purchase_timestamp, approved_at)`. |
| `total_payment_value` | DECIMAL(12,2) | NULL | Sum of all payment rows for this order (via `OUTER APPLY`). |
| `payment_type_primary` | VARCHAR(30) | NULL | First payment method by `payment_sequential`. |
| `payment_installments_max` | INT | NULL | Maximum installments across all payment rows. |
| `payment_methods_count` | INT | NULL | Distinct payment types used for this order. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `order_sk` · **Unique:** `uq_fact_orders_order_id` on `order_id`  
**FKs:** `customer_sk → dim_customer`, `order_purchase_date_sk → dim_date`  
**Indexes:** `customer_unique_id`, `order_status`, `order_purchase_date_sk`, `is_late`.

---

### 3.7. `warehouse.fact_order_items`

**Primary use:** Line-item GMV grain. One row per order line item. `customer_unique_id` denormalised for fast customer-level GMV rollups without joining through `fact_orders`.  
**Fan-out guard:** When joining to `dim_review`, always aggregate `review_score` per `order_id` first — Olist's multi-review-per-order pattern would otherwise multiply GMV.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `item_sk` | INT | NOT NULL | Surrogate key. |
| `order_id` | VARCHAR(50) | NOT NULL | FK to `fact_orders`. |
| `order_item_id` | INT | NOT NULL | Line item sequence within the order. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Denormalised MDM key (no FK; intentional for performance). |
| `product_sk` | INT | NULL | FK to `dim_product`. |
| `seller_sk` | INT | NULL | FK to `dim_seller`. |
| `product_id` | VARCHAR(50) | NULL | Product business key (natural key retained alongside SK). |
| `seller_id` | VARCHAR(50) | NULL | Seller business key. |
| `price` | DECIMAL(12,2) | NOT NULL | Item price (BRL). |
| `freight_value` | DECIMAL(12,2) | NOT NULL | Freight cost for this line item (BRL). |
| `gmv` | DECIMAL(14,2) | **Computed** | `price + freight_value` — Gross Merchandise Value per line item. |
| `freight_ratio` | DECIMAL(6,4) | **Computed** | `freight_value / (price + freight_value)` — freight as % of total spend. |
| `shipping_limit_date` | DATETIME2(3) | NULL | Seller shipping deadline for this item. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `item_sk` · **Unique:** `uq_fact_order_items` on `(order_id, order_item_id)`  
**FKs:** `product_sk → dim_product`, `seller_sk → dim_seller`  
**Indexes:** `order_id`, `customer_unique_id`, `product_sk`, `seller_sk`.

---

## 4. Gold Layer — CRM Mart & Operational Analytics

The Gold layer is the **CRM intelligence and action layer** — BI-ready aggregates, ML feature matrices, and the operational CRM action queue. Fully rebuilt on each `sp_refresh_mart` run. No FK dependencies on Silver — the mart is disposable by design (`TRUNCATE` safe on all tables).

Canonical pipeline constants — declared once in `sp_refresh_mart`, written to `refresh_log`, read by all views:

| Constant | Value | Description |
|----------|-------|-------------|
| `@as_of_date` | `MAX(order_purchase_timestamp)` | Computed fresh each run — never hardcoded. Shared clock for all churn and recency calculations. |
| `@ml_cutoff_date` | `2018-05-01` | EDA-locked train/test split. Features are pre-cutoff; CLV target is post-cutoff. |
| `@churn_window_days` | `180` | Churn definition window (days since last order). EDA-locked. |

---

### 4.1. `mart.refresh_log`

**Primary use:** Shared pipeline clock — a singleton row that every Gold view reads for `as_of_date` and ML parameters, preventing drift between views that would otherwise each independently recompute `MAX(order_date)`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `refresh_id` | INT | NOT NULL | Always 1. Enforced by `CHECK (refresh_id = 1)`. |
| `as_of_date` | DATE | NOT NULL | Snapshot date for this mart refresh (= `MAX(order_purchase_timestamp)`). |
| `ml_cutoff_date` | DATE | NOT NULL | ML train/test split date (`2018-05-01`). |
| `churn_window_days` | INT | NOT NULL | Churn definition in days (180). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Mart refresh timestamp. |

**PK:** `refresh_id` · **Check:** `refresh_id = 1`.

---

### 4.2. `mart.customer_360`

**Primary use:** The unified Customer 360° CRM account record. One row per `customer_unique_id`. The primary Gold table — aggregates orders, GMV, delivery performance, satisfaction, and all ML model outputs into one place.  
**Alignment:** Mirrors the "account record" concept in SAP CRM, Salesforce, and Odoo — the single view of a customer used by retention, success, and revenue teams.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_unique_id` | VARCHAR(50) | NOT NULL | MDM business key. |
| `customer_state` | VARCHAR(5) | NULL | Current state (from `dim_customer` current version). |
| `customer_city` | NVARCHAR(150) | NULL | Current city. |
| `total_orders` | INT | NOT NULL | Lifetime order count. |
| `total_gmv` | DECIMAL(14,2) | NOT NULL | Lifetime Gross Merchandise Value (BRL). |
| `avg_order_value` | DECIMAL(12,2) | NOT NULL | `total_gmv / total_orders`. |
| `total_freight_paid` | DECIMAL(14,2) | NOT NULL | Lifetime freight spend (BRL). |
| `first_order_date` | DATE | NULL | Date of first order. |
| `last_order_date` | DATE | NULL | Date of most recent order. |
| `tenure_days` | INT | NULL | `last_order_date − first_order_date`. 0 for single-order customers. |
| `days_since_last_order` | INT | NULL | `@as_of_date − last_order_date`. Primary recency signal. |
| `avg_review_score` | DECIMAL(4,2) | NULL | Average per-order review score (reviews aggregated per order first to prevent fan-out). |
| `pct_negative_reviews` | DECIMAL(6,4) | NULL | % of orders with average review score ≤ 2. |
| `avg_delivery_delta_days` | DECIMAL(8,2) | NULL | Average `delivery_delta_days` across delivered orders. Negative = consistently early. |
| `pct_late_deliveries` | DECIMAL(6,4) | NULL | % of delivered orders where `is_late = 1`. |
| `is_churned` | BIT | NOT NULL | 1 if `days_since_last_order > 180`. Rule-based churn label; ML churn probability is separate. |
| `customer_health_score` | DECIMAL(6,2) | NOT NULL | 0–100 composite score. See §6 for formula. |
| `health_tier` | VARCHAR(6) | **Computed** | `'High'` (score ≥ 75) · `'Medium'` (≥ 50) · `'Low'` (< 50). Persisted computed column — cannot drift from `customer_health_score`. |
| `churn_probability` | DECIMAL(6,4) | NULL | **Filled by `churn_model.py`.** XGBoost predicted churn probability (0–1). NULL until Phase 5 runs. |
| `clv_predicted_6m` | DECIMAL(14,2) | NULL | **Filled by `clv_model.py`.** Predicted 6-month GMV uplift. NULL for ~25% with no pre-cutoff orders. |
| `avg_sentiment_score` | DECIMAL(6,4) | NULL | **Backfilled by `churn_model.py`** from `mart.sentiment_scores`. LeIA compound average per customer (−1 to 1). NULL for customers with no review text. |
| `expected_next_purchase_days` | DECIMAL(10,1) | NULL | **Filled by `next_purchase.py`.** Weibull AFT median expected days until next order. NULL for single-order customers (out of scope by design). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Mart refresh timestamp. |

**PK:** `customer_unique_id`  
**Indexes:** `customer_state`, `customer_health_score`, `health_tier`, `is_churned`.

---

### 4.3. `mart.rfm_features`

**Primary use:** RFM segmentation and K-means clustering. Powers the Segmentation & RFM dashboard page and feeds the churn model as a feature source.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_unique_id` | VARCHAR(50) | NOT NULL | MDM business key. |
| `recency_days` | INT | NOT NULL | Days since last order as of `@as_of_date`. |
| `frequency` | INT | NOT NULL | Total lifetime order count. |
| `monetary` | DECIMAL(14,2) | NOT NULL | Total lifetime GMV (BRL). |
| `recency_score` | TINYINT | NOT NULL | `NTILE(5) ORDER BY recency_days DESC` — 5 = most recent. |
| `frequency_score` | TINYINT | NOT NULL | `NTILE(5) ORDER BY frequency DESC` — 5 = most frequent. |
| `monetary_score` | TINYINT | NOT NULL | `NTILE(5) ORDER BY monetary DESC` — 5 = highest spend. |
| `rfm_score` | VARCHAR(3) | NOT NULL | Concatenation of three scores (e.g., `'555'` = Champions, `'111'` = Lost). |
| `rfm_segment` | NVARCHAR(30) | NULL | **Filled by `segmentation.py`.** Business segment label. Rule set verified exhaustive across all 125 possible score combinations. |
| `km_cluster` | TINYINT | NULL | **Filled by `segmentation.py`.** K-means cluster ID (0–6; K=7, silhouette=0.3106). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Mart refresh timestamp. |

**PK:** `customer_unique_id` · **Indexes:** `rfm_score`, `rfm_segment`.

**Segment reference:**

| Segment | Count | Churn Rate | Strategic Posture |
|---------|-------|------------|-------------------|
| Frequent Low-Spender | 22,481 | 64.0% | Frequency without basket — engagement play |
| Needs Attention | 14,167 | 100% | Sliding; intervention warranted |
| Loyal | 12,841 | 35.3% | Strong base; monitor for drift |
| Hibernating | 12,584 | 100% | Dormant; cost-efficient reactivation |
| Potential Loyalist | 10,572 | 27.3% | Recent; nurture to Loyal |
| At Risk | 10,423 | 100% | Were valuable; recency dropped |
| Can't Lose | 8,846 | 100% | High historical value; now dormant |
| **Champions** | **3,807** | **0%** | Best customers; retention strategy working |
| Lost | 375 | 100% | Long-lapsed; minimal intervention value |

---

### 4.4. `mart.clv_features`

**Primary use:** ML feature matrix for Customer Lifetime Value (CLV) regression. `actual_gmv_post_cutoff` is the supervised learning target. Features are computed from pre-cutoff warehouse data to prevent leakage.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_unique_id` | VARCHAR(50) | NOT NULL | MDM business key. |
| `avg_order_value` | DECIMAL(12,2) | NOT NULL | Average order value pre-cutoff. |
| `order_frequency_per_month` | DECIMAL(10,4) | NULL | `total_orders / (tenure_days / 30.0)`. NULL for single-order customers (tenure = 0); imputed to 0.0 in `clv_model.py` — not the multi-order median, which would falsely assign high frequency to 98% of customers. |
| `tenure_months` | DECIMAL(10,2) | NULL | `tenure_days / 30.0`. NULL for single-order customers. |
| `total_categories_purchased` | INT | NOT NULL | Distinct English product categories purchased pre-cutoff. Category diversity proxy. |
| `avg_review_score` | DECIMAL(4,2) | NULL | Average review score pre-cutoff. |
| `avg_delivery_delta` | DECIMAL(8,2) | NULL | Average delivery delta (days) pre-cutoff. |
| `pct_late` | DECIMAL(6,4) | NULL | % late deliveries pre-cutoff. |
| `customer_state` | VARCHAR(5) | NULL | One-hot encoded in `clv_model.py`. |
| `days_since_last_order` | INT | NULL | Recency signal (strongest single predictor per feature importance). |
| `preferred_payment_type` | VARCHAR(30) | NULL | Most frequent payment method pre-cutoff. Tiebreak: highest total payment value, then alphabetical. |
| `actual_gmv_post_cutoff` | DECIMAL(14,2) | NOT NULL | **ML target variable.** GMV from orders on or after `@ml_cutoff_date (2018-05-01)`. 99.21% zero — zero-inflated target. |
| `clv_predicted_6m` | DECIMAL(14,2) | NULL | **Filled by `clv_model.py`.** XGBoost point estimate. |
| `clv_ci_lower` | DECIMAL(14,2) | NULL | Lower bound of 80% prediction interval (α=0.1 quantile model). |
| `clv_ci_upper` | DECIMAL(14,2) | NULL | Upper bound of 80% prediction interval (α=0.9 quantile model). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Mart refresh timestamp. |

**PK:** `customer_unique_id` · **Indexes:** `customer_state`, `days_since_last_order`.

---

### 4.5. `mart.sentiment_scores`

**Primary use:** Per-review NLP sentiment scores from LeIA (Portuguese VADER). Powers the Sentiment & NLP dashboard and feeds `avg_sentiment_score` into the churn model.  
**Coverage:** 40,641 reviews scored (41.29% of total). Empty reviews not scored — `compound_score` NULL is "no text", not "neutral".

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `review_id` | VARCHAR(50) | NOT NULL | Review business key. |
| `order_id` | VARCHAR(50) | NOT NULL | Associated order. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | MDM key — enables customer-level sentiment aggregation. |
| `review_score` | TINYINT | NOT NULL | Original 1–5 satisfaction score. |
| `review_comment_message` | NVARCHAR(MAX) | NULL | Review body (Portuguese). Retained for reference and reanalysis. |
| `review_creation_date` | DATETIME2(3) | NULL | Review creation date. |
| `compound_score` | DECIMAL(6,4) | NULL | **Filled by `sentiment.py`.** LeIA compound score (−1.0 = most negative, +1.0 = most positive). NULL if no review text. |
| `sentiment_label` | VARCHAR(10) | NULL | **Filled by `sentiment.py`.** `'positive'` (compound ≥ 0.05) · `'neutral'` (−0.05 to 0.05) · `'negative'` (< −0.05). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Mart refresh timestamp. |

**PK:** `review_id` · **Index:** `customer_unique_id`.

---

### 4.6. `mart.crm_action_queue`

**Primary use:** **Operational CRM Action Engine output.** ML predictions translated into prioritised, human-readable CRM action records — the same pattern used in SAP CRM campaign automation and Salesforce action queues. One row per action event per customer per run.  
**Grain note:** PK is `action_id` (surrogate identity) — multiple rows per customer accumulate across runs. `vw_customer_health` selects only the latest per customer via `ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY created_at DESC)`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `action_id` | INT | NOT NULL | Surrogate key (identity). |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | MDM business key. |
| `action_type` | VARCHAR(30) | NOT NULL | CRM action assigned. See action reference below. |
| `priority` | VARCHAR(10) | NOT NULL | `'HIGH'`, `'MED'`, or `'LOW'`. |
| `churn_probability` | DECIMAL(6,4) | NULL | Model output at time of action generation (snapshot). |
| `clv_predicted` | DECIMAL(14,2) | NULL | Predicted CLV at time of action generation (snapshot). |
| `trigger_reason` | NVARCHAR(300) | NOT NULL | Human-readable explanation — e.g., *"Churn risk 0.837 ≥ 0.60; CLV R$0.96 at 55th pct — high-value customer, premium retention warranted."* |
| `created_at` | DATETIME2(3) | NOT NULL | Action record creation timestamp. |

**PK:** `action_id`  
**Check constraints:** `action_type IN ('RETENTION_CAMPAIGN','REACTIVATION','VIP_UPGRADE','MONITOR')` · `priority IN ('HIGH','MED','LOW')`  
**Indexes:** `customer_unique_id`, `(priority, action_type)`.

**Action reference:**

| Action Type | Priority | Trigger Condition | Business Intent |
|-------------|----------|-------------------|-----------------|
| `RETENTION_CAMPAIGN` | HIGH | `churn_probability ≥ 0.60` AND `clv_predicted > median` | High-value at-risk customer — premium retention spend justified |
| `REACTIVATION` | MED | `churn_probability ≥ 0.60` AND `clv_predicted ≤ median` | Lower-value at-risk — cost-efficient reactivation nudge |
| `VIP_UPGRADE` | MED | Champions segment AND `clv_predicted > p90` | Best customers — VIP recognition and loyalty deepening |
| `MONITOR` | LOW | All others — no high-priority conditions met | Healthy or low-signal customers; no active intervention |

**Distribution (current run):** RETENTION_CAMPAIGN: 11,957 (12.4%) · REACTIVATION: 26,574 (27.7%) · MONITOR: 57,565 (59.9%).

---

### 4.7. `mart.action_run_log`

**Primary use:** Audit and governance log for every `action_rules.py` execution. Tracks thresholds, counts, and config snapshot — supports threshold change management and pipeline auditability.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `run_id` | INT | NOT NULL | Surrogate key (identity). |
| `run_timestamp` | DATETIME2(3) | NOT NULL | Run start timestamp (UTC). |
| `run_by` | NVARCHAR(100) | NULL | OS username of the process that ran the script. |
| `script_version` | VARCHAR(20) | NULL | `action_rules.py` version string. |
| `churn_threshold_used` | FLOAT | NOT NULL | Churn probability threshold applied for this run. |
| `clv_percentile_used` | INT | NOT NULL | CLV percentile used for RETENTION vs REACTIVATION split. |
| `vip_percentile_used` | INT | NOT NULL | CLV percentile threshold for `VIP_UPGRADE` eligibility. |
| `write_mode` | VARCHAR(30) | NOT NULL | `'TRUNCATE_INSERT'` (live run) or `'DRY_RUN'` (no DB writes). |
| `n_retention_campaign` | INT | NOT NULL | Count of `RETENTION_CAMPAIGN` actions this run. |
| `n_reactivation` | INT | NOT NULL | Count of `REACTIVATION` actions. |
| `n_vip_upgrade` | INT | NOT NULL | Count of `VIP_UPGRADE` actions. |
| `n_at_risk_nurture` | INT | NOT NULL | Reserved (not currently active). |
| `n_monitor` | INT | NOT NULL | Count of `MONITOR` actions. |
| `n_total` | INT | NOT NULL | Total customers processed. |
| `n_priority_high` | INT | NOT NULL | Total HIGH-priority actions. |
| `n_priority_med` | INT | NOT NULL | Total MED-priority actions. |
| `n_priority_low` | INT | NOT NULL | Total LOW-priority actions. |
| `n_customers_in` | INT | NOT NULL | Rows read from `vw_customer_health` as input. |
| `n_customers_unmatched` | INT | NOT NULL | Customers with no action rule match (should always be 0 — MONITOR is the catch-all). |
| `config_snapshot` | NVARCHAR(MAX) | NULL | Full JSON snapshot of `action_rules.json` used for this run. |
| `run_notes` | NVARCHAR(500) | NULL | Optional notes (e.g., reason for threshold change). |

**PK:** `run_id` · **Index:** `run_timestamp DESC`.

---

## 5. Analytical Views — BI Consumption Layer

Three views in the `mart` schema serve as the **exclusive data source for Power BI** — no dashboard page queries mart tables directly. This separation keeps the BI layer decoupled from mart table structure changes.

All views `CROSS JOIN mart.refresh_log` for the shared `as_of_date` clock.

---

### 5.1. `mart.vw_customer_health`

**Primary use:** Single flat view for the Customer 360 dashboard and all Power BI pages that need customer-level data. One row per `customer_unique_id`.  
**Joins:** `customer_360` + `rfm_features` + `clv_features` + latest action from `crm_action_queue` (via `ROW_NUMBER()`) + `refresh_log`.

**Key derived columns (not in underlying tables):**

| Column | Derivation | Purpose |
|--------|------------|---------|
| `recency_band` | `CASE` on `days_since_last_order`: 0–30d · 31–90d · 91–180d · 180+d | Slicer-friendly recency grouping |
| `recency_tier` | `CASE` on `recency_score`: ≥4 = High · 3 = Medium · <3 = Low | Derived from existing NTILE scores; cannot drift |
| `frequency_tier` | Same pattern on `frequency_score` | |
| `monetary_tier` | Same pattern on `monetary_score` | |
| `latest_action_type` | Latest `action_type` per customer from `crm_action_queue` | CRM action status |
| `latest_action_priority` | Latest `priority` per customer | Action triage |
| `latest_action_reason` | Latest `trigger_reason` per customer | Human-readable on Customer 360 page |
| `as_of_date` | From `refresh_log` | Shared pipeline clock |

---

### 5.2. `mart.vw_churn_signals`

**Primary use:** Retention triage — at-risk customers with explanatory signals. Powers the Churn & Action Risk dashboard page.  
**Filter:** `is_churned = 1` OR `churn_probability > 0.4`.

**Key derived columns:**

| Column | Description | Business Use |
|--------|-------------|--------------|
| `churn_driver_summary` | Rule-vs-model agreement: `'Rule + model agree'` / `'Rule-based only'` / `'Model-flagged'` | Validates churn signal source |
| `primary_driver` | Worst dimension for this customer: `'Delivery experience'` · `'Low satisfaction'` · `'Lapsed / inactive'` · `'Low historical value'` | Root-cause triage for retention teams |
| `urgency_score` | 0–100 weighted triage score (see §6) | Operational prioritisation within churn queue |

**Urgency score components:**
- 40% churn probability (or rule-based `is_churned` fallback before ML runs)
- 40% value at stake (1 − monetary_badness — higher value = higher urgency)
- 20% timing pressure (days_since_last_order / 360, capped at 1.0)

---

### 5.3. `mart.vw_geo_performance`

**Primary use:** Regional territory intelligence — state-level CRM performance aggregates. Powers the Geo Intelligence dashboard page.

| Column | Description |
|--------|-------------|
| `customer_state` | Two-letter state code. |
| `customer_count` | Unique customers in this state. |
| `total_gmv` | Total GMV (BRL) from this state. |
| `pct_of_total_gmv` | This state's share of national GMV. |
| `pct_of_total_customers` | Share of customer base. |
| `avg_delivery_delta_days` | Average delivery delta (negative = early). |
| `pct_late_deliveries` | % of deliveries that were late. |
| `churn_rate_pct` | % of customers flagged as churned. |
| `avg_health_score` | Average `customer_health_score` for this state. |
| `dashboard_state_label` | States with <2% GMV share collapsed to `'Other'`. Prevents map clutter. |

---

## 6. Business Definitions, Metrics & Formulas

| Concept | Definition | Business Use |
|---------|------------|--------------|
| **Churn** | `days_since_last_order > 180` evaluated at `@as_of_date (2018-10-17)`. | Rule-based churn label. 71.18% baseline churn rate reflects structural one-time-buyer dominance. Used as ML target (`is_churned`) and RevOps KPI. |
| **GMV** | `price + freight_value` per line item, summed per order or customer. | Primary revenue metric. Total platform GMV: R$15.84M. |
| **Delivery Delta** | `DATEDIFF(DAY, order_estimated_delivery_date, order_delivered_customer_date)`. Negative = early; positive = late. | Key churn predictor. Strongly correlated with review score. |
| **Customer Health Score** | `(recency_pct × 0.4) + (monetary_pct × 0.4) + (satisfaction_pct × 0.2) × 100` where each component is `PERCENT_RANK()`. | Composite CRM health KPI. `PERCENT_RANK()` used (not linear max-scale) to prevent whale-customer distortion. |
| **Recency Score (RFM)** | `NTILE(5) OVER (ORDER BY recency_days DESC)`. 5 = most recent. | RFM R dimension. Descending order — lower recency days = higher score = better. |
| **Frequency Score (RFM)** | `NTILE(5) OVER (ORDER BY total_orders DESC)`. 5 = most frequent. | RFM F dimension. |
| **Monetary Score (RFM)** | `NTILE(5) OVER (ORDER BY total_gmv DESC)`. 5 = highest spend. | RFM M dimension. |
| **RFM Segment** | Business label assigned by `segmentation.py` based on exhaustive rules covering all 125 `(R,F,M)` score combinations. | Customer strategy segmentation for retention and RevOps. |
| **CLV Target** | `actual_gmv_post_cutoff` — GMV from orders with `order_purchase_timestamp ≥ 2018-05-01`. | Supervised CLV model target. 99.21% zero (structural one-time-buyer effect). |
| **CLV Prediction** | XGBoost point estimate + 80% quantile interval (α=0.1 / α=0.9). | Relative triage score for action queue CLV split. Absolute R$ values unreliable below 90th percentile due to zero-inflated target. |
| **Urgency Score** | `0.4 × churn_probability + 0.4 × (1 − monetary_badness) + 0.2 × min(days_since_last_order / 360, 1.0)` × 100 | Operational retention prioritisation within the churn queue. |
| **Action Rules** | Evaluated in priority order by `action_rules.py`: `RETENTION_CAMPAIGN` → `REACTIVATION` → `VIP_UPGRADE` → `MONITOR` (catch-all). Rules and thresholds defined in `action_rules.json`. | CRM Action Engine. Translates ML predictions into operational tasks — aligned with SAP CRM / Salesforce campaign automation patterns. |
| **Satisfaction Imputation** | Customers with zero reviews receive population median `avg_review_score` via `PERCENTILE_CONT(0.5)` — not 0. | Prevents penalising silent customers as if they left the worst possible review. Affects `customer_health_score` computation for 58.71% of customers. |

---

## 7. Conceptual Data Model & Relationships

### Silver Layer (Star Schema)

```
warehouse.dim_date (PK date_sk)
    └── warehouse.fact_orders (FK order_purchase_date_sk)

warehouse.dim_customer (PK customer_sk)  [SCD Type 2 — MDM]
    └── warehouse.fact_orders (FK customer_sk — point-in-time join)

warehouse.dim_product (PK product_sk)
    └── warehouse.fact_order_items (FK product_sk)

warehouse.dim_seller (PK seller_sk)
    └── warehouse.fact_order_items (FK seller_sk)

warehouse.fact_orders (PK order_sk)
    └── warehouse.fact_order_items (join on order_id — no FK, intentional)

warehouse.dim_review (no FKs; joined on order_id / customer_unique_id)
```

**Design note:** `fact_order_items` does not FK to `fact_orders` — this avoids constraint conflicts during idempotent rebuilds and matches the Kimball pattern for degenerate dimension handling.

### Gold Layer (CRM Mart)

```
mart.customer_360 (PK customer_unique_id)
    ├── mart.rfm_features (PK customer_unique_id — 1:1)
    ├── mart.clv_features (PK customer_unique_id — 1:1)
    ├── mart.crm_action_queue (FK customer_unique_id — 1:Many; latest via ROW_NUMBER())
    └── mart.vw_customer_health (flat view joining all of the above + refresh_log)

mart.sentiment_scores (PK review_id — indexed on customer_unique_id)
    └── Aggregated to customer_360.avg_sentiment_score by churn_model.py

mart.refresh_log (singleton — CROSS JOINed into all three views)
mart.action_run_log (append-only audit log — no joins to business tables)
```

**Gold design principles:**
- No FK constraints between Gold tables — mart is fully disposable (`TRUNCATE` safe on all tables)
- All Gold tables share `customer_unique_id` as the join key — no surrogate keys at this layer
- Python ML writes predictions back to existing Gold columns — no new tables created by ML pipeline

---

## 8. Power BI & Stakeholder Usage Guide

### 8.1 Recommended Data Source Configuration

| Table / View | Import Mode | Rationale |
|-------------|-------------|-----------|
| `mart.vw_customer_health` | **Import** | Complex joins + window functions; DirectQuery latency unacceptable for 96k-row analytical queries |
| `mart.vw_churn_signals` | **Import** | Inherits from `vw_customer_health`; same rationale |
| `mart.vw_geo_performance` | **Import** | Pre-aggregated; fast to import, no DirectQuery benefit |
| `mart.sentiment_scores` | **Import** | Used for per-review NLP visuals |
| `mart.crm_action_queue` | **Import** | Action history for audit and trend views |
| `mart.action_run_log` | **Import** | Pipeline audit view |

**All heavy computation — health scores, RFM scores, CLV, churn probability — is pre-computed in SQL. Do not recreate in DAX.**

### 8.2 Recommended DAX Measures

| Measure | Formula (Conceptual) | Dashboard Use |
|---------|----------------------|---------------|
| `Total GMV` | `SUM(CustomerHealth[total_gmv])` | Command Centre KPI |
| `Avg Churn Probability` | `AVERAGE(CustomerHealth[churn_probability])` | Churn & Action Risk |
| `Actionable Customers` | `COUNTROWS(FILTER(CustomerHealth, [latest_action_type] <> "MONITOR"))` | Command Centre |
| `HIGH Priority Count` | `COUNTROWS(FILTER(CustomerHealth, [latest_action_priority] = "HIGH"))` | Retention triage |
| `Churn Rate` | `DIVIDE(COUNTROWS(FILTER(CustomerHealth, [is_churned] = 1)), [Total Customers])` | KPI headline |
| `Avg Health Score` | `AVERAGE(CustomerHealth[customer_health_score])` | Portfolio health |

### 8.3 Recommended Dimensions for Slicers

| Slicer | Field | Page |
|--------|-------|------|
| Customer segment | `rfm_segment` | Segmentation & RFM, Command Centre |
| Health tier | `health_tier` | All pages |
| Recency band | `recency_band` | Churn & Action Risk, Customer 360 |
| State / territory | `customer_state` | Geo Intelligence |
| Action type | `latest_action_type` | Command Centre, Churn & Action Risk |
| Sentiment label | `sentiment_label` | Sentiment & NLP |

### 8.4 Key Filters — Apply Before Publishing

| Dashboard Page | Required Filter | Reason |
|----------------|----------------|--------|
| Churn & Action Risk | Use `vw_churn_signals` OR filter `is_churned = 1` | Removes non-churned customers from triage table |
| Sentiment & NLP | `compound_score IS NOT NULL` for NLP visuals | Excludes reviews without text (NULL ≠ neutral) |
| Geo Intelligence | Use `dashboard_state_label` (not `customer_state` raw) | Collapses <2% GMV states into 'Other' |
| Customer 360 | Set as drill-through target only | Prevent direct navigation; preserve as detail page |

### 8.5 NULL Handling Reference

| Column | NULL Meaning | Recommended Treatment in Power BI |
|--------|-------------|-----------------------------------|
| `avg_sentiment_score` | Customer left no review text | Display as "No data" — do not substitute 0 or "Neutral" |
| `expected_next_purchase_days` | Single-order customer or survival curve never crosses 50% | Exclude from next-purchase visuals; use `is_churned` for churn status |
| `clv_predicted_6m` | Customer had no pre-cutoff order (excluded from CLV model) | Substitute 0 only for aggregated visuals; display as "N/A" in Customer 360 card |
| `churn_probability` | ML pipeline has not run yet | Display "Pending" — do not default to 0.5 |
| `km_cluster` | Segmentation has not run | Same as above |

### 8.6 Self-Service Analytics — What Non-Technical Users Can Do

This platform is designed as a **stakeholder-facing analytics platform** where business users operate entirely in Power BI — no SQL or Python knowledge required.

| User Role | Dashboard Page | Self-Service Capability |
|-----------|---------------|------------------------|
| Retention Manager | Churn & Action Risk | Adjust churn threshold via What-If parameter; drill to any customer's 360 record |
| CRM Analyst | Segmentation & RFM | Toggle between rule-based RFM segments and K-means clusters via bookmark |
| Territory Manager | Geo Intelligence | Switch map metric (GMV / Churn rate / Late % / Review score) via bookmark |
| CX Analyst | Sentiment & NLP | Filter by sentiment label; toggle sarcasm detection panel |
| Customer Success | Customer 360 | Drill-through from any table on any page to full customer account record |
| CEO / Head of Retention | Command Centre | Full portfolio snapshot; no filtering required |

---

*Data Dictionary — Enterprise CRM Intelligence & Customer 360 Platform · v1.0 · Abdallah A Khames · BODZZ · July 2026*