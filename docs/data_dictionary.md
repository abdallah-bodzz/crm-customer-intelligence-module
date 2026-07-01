# Data Dictionary — CRM Customer Intelligence Module

**Version:** 1.0  
**Lead dev** Abdallah A Khames  
**company** BODZZ   
**github** `abdallah-bodzz`     
**repo** https://github.com/abdallah-bodzz/crm-customer-intelligence-module     
**Database:** `CRM_Analytics`  
**Medallion Layers:** `staging` (Bronze), `warehouse` (Silver), `mart` (Gold)  
**Last Updated:** 2026-06  

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Bronze Layer — Staging Schema](#2-bronze-layer--staging-schema)
   - 2.1. `stg_customers`
   - 2.2. `stg_sellers`
   - 2.3. `stg_products`
   - 2.4. `stg_geolocation`
   - 2.5. `stg_orders`
   - 2.6. `stg_order_items`
   - 2.7. `stg_order_payments`
   - 2.8. `stg_order_reviews`
   - 2.9. `stg_product_category_translation`
3. [Silver Layer — Warehouse Schema](#3-silver-layer--warehouse-schema)
   - 3.1. `dim_date`
   - 3.2. `dim_customer`
   - 3.3. `dim_seller`
   - 3.4. `dim_product`
   - 3.5. `dim_review`
   - 3.6. `fact_orders`
   - 3.7. `fact_order_items`
4. [Gold Layer — Mart Schema](#4-gold-layer--mart-schema)
   - 4.1. `refresh_log`
   - 4.2. `customer_360`
   - 4.3. `rfm_features`
   - 4.4. `clv_features`
   - 4.5. `sentiment_scores`
   - 4.6. `crm_action_queue`
   - 4.7. `action_run_log`
5. [Analytical Views](#5-analytical-views)
   - 5.1. `vw_customer_health`
   - 5.2. `vw_churn_signals`
   - 5.3. `vw_geo_performance`
6. [Business Definitions & Formulas](#6-business-definitions--formulas)
7. [Relationship Diagram (Conceptual)](#7-relationship-diagram-conceptual)
8. [Guidance for Power BI Developers](#8-guidance-for-power-bi-developers)

---

## 1. Overview & Architecture

The database follows the **medallion lakehouse** pattern:

| Layer | Schema | Purpose |
|-------|--------|---------|
| **Bronze** | `staging` | Raw, unmodified ingestion from CSV files. Tables mirror source columns; include audit columns (`load_timestamp`, `source_file`). No constraints or business logic. |
| **Silver** | `warehouse` | Cleaned, typed, and modeled. Dimension and fact tables with SCD Type 2 for customers, persisted computed columns, foreign keys, and indexes. The single source of truth for business entities. |
| **Gold** | `mart` | Aggregated, pre‑joined, BI‑ready tables. Also contains ML feature tables and CRM action queue. Refreshed daily by `sp_refresh_mart`. |

All Python ML scripts read from and write to the `mart` schema.

---

## 2. Bronze Layer — Staging Schema

All staging tables are append‑only and contain audit columns:
- `load_timestamp`: when the row was ingested (UTC).
- `source_file`: name of the CSV file that provided the row.

### 2.1. `stg_customers`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_id` | VARCHAR(50) | NOT NULL | Operational customer identifier (per order). |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Real customer identifier (stable across orders). The business key used in Silver. |
| `customer_zip_code_prefix` | VARCHAR(10) | NULL | First part of Brazilian ZIP code (preserves leading zeros). |
| `customer_city` | NVARCHAR(150) | NULL | City name (PT‑BR, may contain accents). |
| `customer_state` | VARCHAR(5) | NULL | Two‑letter state code. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_customers_dataset.csv` |

**Source:** `olist_customers_dataset.csv`

---

### 2.2. `stg_sellers`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `seller_id` | VARCHAR(50) | NOT NULL | Seller identifier. |
| `seller_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix. |
| `seller_city` | NVARCHAR(150) | NULL | Seller city. |
| `seller_state` | VARCHAR(5) | NULL | Seller state. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_sellers_dataset.csv` |

**Source:** `olist_sellers_dataset.csv`

---

### 2.3. `stg_products`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `product_id` | VARCHAR(50) | NOT NULL | Product identifier. |
| `product_category_name` | NVARCHAR(100) | NULL | Category name in Portuguese. |
| `product_name_lenght` | FLOAT | NULL | **Note:** Original CSV misspells “length”. Length of product name (characters). |
| `product_description_lenght` | FLOAT | NULL | **Note:** Misspelled. Description length. |
| `product_photos_qty` | FLOAT | NULL | Number of photos. |
| `product_weight_g` | FLOAT | NULL | Weight in grams. |
| `product_length_cm` | FLOAT | NULL | Length in cm. |
| `product_height_cm` | FLOAT | NULL | Height in cm. |
| `product_width_cm` | FLOAT | NULL | Width in cm. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_products_dataset.csv` |

**Source:** `olist_products_dataset.csv`

---

### 2.4. `stg_geolocation`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `geolocation_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix. |
| `geolocation_lat` | FLOAT | NULL | Latitude. |
| `geolocation_lng` | FLOAT | NULL | Longitude. |
| `geolocation_city` | NVARCHAR(150) | NULL | City. |
| `geolocation_state` | VARCHAR(5) | NULL | State. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_geolocation_dataset.csv` |

**Source:** `olist_geolocation_dataset.csv`  
**Note:** May contain duplicate rows; Silver layer handles deduplication.

---

### 2.5. `stg_orders`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_id` | VARCHAR(50) | NOT NULL | Order identifier. |
| `customer_id` | VARCHAR(50) | NOT NULL | Customer operational key. |
| `order_status` | VARCHAR(20) | NULL | e.g., `delivered`, `canceled`, `shipped`. |
| `order_purchase_timestamp` | DATETIME2(3) | NULL | Timestamp of purchase. |
| `order_approved_at` | DATETIME2(3) | NULL | Payment approval timestamp. |
| `order_delivered_carrier_date` | DATETIME2(3) | NULL | Date handed to carrier. |
| `order_delivered_customer_date` | DATETIME2(3) | NULL | Date delivered to customer. |
| `order_estimated_delivery_date` | DATETIME2(3) | NULL | Estimated delivery date. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_orders_dataset.csv` |

**Source:** `olist_orders_dataset.csv`

---

### 2.6. `stg_order_items`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_id` | VARCHAR(50) | NOT NULL | Order identifier. |
| `order_item_id` | INT | NOT NULL | Sequence number within the order. |
| `product_id` | VARCHAR(50) | NOT NULL | Product identifier. |
| `seller_id` | VARCHAR(50) | NOT NULL | Seller identifier. |
| `shipping_limit_date` | DATETIME2(3) | NULL | Deadline for shipping. |
| `price` | DECIMAL(12,2) | NULL | Item price (BRL). |
| `freight_value` | DECIMAL(12,2) | NULL | Shipping cost (BRL). |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_order_items_dataset.csv` |

**Source:** `olist_order_items_dataset.csv`

---

### 2.7. `stg_order_payments`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_id` | VARCHAR(50) | NOT NULL | Order identifier. |
| `payment_sequential` | INT | NOT NULL | Sequence of payment for the order. |
| `payment_type` | VARCHAR(30) | NULL | e.g., `credit_card`, `boleto`, `voucher`. |
| `payment_installments` | INT | NULL | Number of installments. |
| `payment_value` | DECIMAL(12,2) | NULL | Payment amount (BRL). |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_order_payments_dataset.csv` |

**Source:** `olist_order_payments_dataset.csv`

---

### 2.8. `stg_order_reviews`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `review_id` | VARCHAR(50) | NOT NULL | Review identifier (may not be globally unique; deduplicated in Silver). |
| `order_id` | VARCHAR(50) | NOT NULL | Order identifier. |
| `review_score` | FLOAT | NULL | Score 1–5. |
| `review_comment_title` | NVARCHAR(200) | NULL | Review title. |
| `review_comment_message` | NVARCHAR(MAX) | NULL | Review body (free text, Portuguese). |
| `review_creation_date` | DATETIME2(3) | NULL | Date review was created. |
| `review_answer_timestamp` | DATETIME2(3) | NULL | Date seller responded. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `olist_order_reviews_dataset.csv` |

**Source:** `olist_order_reviews_dataset.csv`

---

### 2.9. `stg_product_category_translation`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `product_category_name` | NVARCHAR(100) | NOT NULL | Category name in Portuguese. |
| `product_category_name_english` | NVARCHAR(100) | NOT NULL | English translation. |
| `load_timestamp` | DATETIME2(3) | NOT NULL | Ingestion timestamp. |
| `source_file` | VARCHAR(100) | NOT NULL | `product_category_name_translation.csv` |

**Source:** `product_category_name_translation.csv`

---

## 3. Silver Layer — Warehouse Schema

### 3.1. `dim_date`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `date_sk` | INT | NOT NULL | Surrogate key (YYYYMMDD). |
| `full_date` | DATE | NOT NULL | Calendar date. |
| `year` | SMALLINT | NOT NULL | Year. |
| `quarter` | TINYINT | NOT NULL | Quarter (1–4). |
| `month` | TINYINT | NOT NULL | Month (1–12). |
| `month_name` | NVARCHAR(12) | NOT NULL | Month name. |
| `week_of_year` | TINYINT | NOT NULL | ISO week number. |
| `day_of_month` | TINYINT | NOT NULL | Day of month. |
| `day_name` | NVARCHAR(12) | NOT NULL | Day name. |
| `is_weekend` | BIT | NOT NULL | 1 if Saturday/Sunday. |
| `is_brazilian_holiday` | BIT | NOT NULL | 1 for known fixed holidays (New Year, Tiradentes, Labour Day, Independence Day, etc.). |
| `holiday_name` | NVARCHAR(100) | NULL | Name of holiday if applicable. |
| `fiscal_year` | SMALLINT | NOT NULL | Aligns with calendar year (Jan–Dec). |
| `fiscal_quarter` | TINYINT | NOT NULL | Aligns with calendar quarter. |

**PK:** `date_sk`  
**Notes:** Covers 2015-01-01 to 2020-12-31, plus a sentinel row `1900-01-01` for unknown dates.

---

### 3.2. `dim_customer`

SCD Type 2 table tracking customer attribute changes (city/state/zip).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_sk` | INT | NOT NULL | Surrogate key (identity). |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Business key (real person). |
| `customer_id` | VARCHAR(50) | NOT NULL | Most recent operational `customer_id` for this version. |
| `customer_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix. |
| `customer_city` | NVARCHAR(150) | NULL | City. |
| `customer_state` | VARCHAR(5) | NULL | State. |
| `valid_from` | DATE | NOT NULL | Start date of version. |
| `valid_to` | DATE | NULL | End date; NULL means current. |
| `is_current` | BIT | NOT NULL | 1 for the latest version. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |
| `updated_at` | DATETIME2(3) | NOT NULL | Last update timestamp. |

**PK:** `customer_sk`  
**Unique index:** `ux_dim_customer_current` on `customer_unique_id` WHERE `is_current = 1` ensures exactly one current row per business key.  
**Indexes:** on `customer_unique_id` (include `is_current`), `customer_id`, `customer_state`.

---

### 3.3. `dim_seller`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `seller_sk` | INT | NOT NULL | Surrogate key (identity). |
| `seller_id` | VARCHAR(50) | NOT NULL | Seller business key. |
| `seller_zip_code_prefix` | VARCHAR(10) | NULL | ZIP prefix. |
| `seller_city` | NVARCHAR(150) | NULL | City. |
| `seller_state` | VARCHAR(5) | NULL | State. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `seller_sk`  
**Unique constraint:** `uq_dim_seller_id` on `seller_id`.

---

### 3.4. `dim_product`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `product_sk` | INT | NOT NULL | Surrogate key (identity). |
| `product_id` | VARCHAR(50) | NOT NULL | Business key. |
| `product_category_name` | NVARCHAR(100) | NULL | Original PT‑BR category. |
| `product_category_name_english` | NVARCHAR(100) | NOT NULL | English category; `'UNKNOWN'` if missing. |
| `product_name_length` | INT | NULL | Product name length (corrected spelling). |
| `product_description_length` | INT | NULL | Description length. |
| `product_photos_qty` | INT | NULL | Number of photos. |
| `product_weight_g` | FLOAT | NULL | Weight in grams. |
| `product_length_cm` | FLOAT | NULL | Length in cm. |
| `product_height_cm` | FLOAT | NULL | Height in cm. |
| `product_width_cm` | FLOAT | NULL | Width in cm. |
| `product_volume_cm3` | FLOAT | **Computed** | `length * height * width` if all non‑NULL; else NULL. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `product_sk`  
**Unique constraint:** `uq_dim_product_id` on `product_id`.

---

### 3.5. `dim_review`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `review_sk` | INT | NOT NULL | Surrogate key (identity). |
| `review_id` | VARCHAR(50) | NOT NULL | Review identifier (deduplicated in Silver). |
| `order_id` | VARCHAR(50) | NOT NULL | Order identifier. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Denormalised customer key for fast aggregation. |
| `review_score` | TINYINT | NOT NULL | Score 1–5. |
| `review_comment_title` | NVARCHAR(200) | NULL | Title. |
| `review_comment_message` | NVARCHAR(MAX) | NULL | Review body. |
| `has_comment` | BIT | **Computed** | 1 if `review_comment_message` is non‑empty. |
| `review_creation_date` | DATETIME2(3) | NULL | Date review created. |
| `review_answer_timestamp` | DATETIME2(3) | NULL | Date seller answered. |
| `response_delay_days` | INT | **Computed** | `DATEDIFF(DAY, review_creation_date, review_answer_timestamp)`. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `review_sk`  
**Unique constraint:** `uq_dim_review_id` on `review_id` (guaranteed after dedupe).  
**Check:** `review_score BETWEEN 1 AND 5`.

---

### 3.6. `fact_orders`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `order_sk` | INT | NOT NULL | Surrogate key (identity). |
| `customer_sk` | INT | NOT NULL | FK to `dim_customer`. |
| `order_purchase_date_sk` | INT | NOT NULL | FK to `dim_date` (purchase date). |
| `order_id` | VARCHAR(50) | NOT NULL | Natural key. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Denormalised customer business key. |
| `customer_id` | VARCHAR(50) | NOT NULL | Operational customer key. |
| `order_status` | VARCHAR(20) | NULL | Order status. |
| `is_delivered` | BIT | **Computed** | 1 if `order_status = 'delivered'`. |
| `is_canceled` | BIT | **Computed** | 1 if `order_status = 'canceled'`. |
| `order_purchase_timestamp` | DATETIME2(3) | NULL | Purchase timestamp. |
| `order_approved_at` | DATETIME2(3) | NULL | Approval timestamp. |
| `order_delivered_carrier_date` | DATETIME2(3) | NULL | Carrier handover. |
| `order_delivered_customer_date` | DATETIME2(3) | NULL | Customer delivery. |
| `order_estimated_delivery_date` | DATETIME2(3) | NULL | Estimated delivery. |
| `delivery_delta_days` | INT | **Computed** | `DATEDIFF(DAY, estimated, actual)`; negative = early. |
| `is_late` | BIT | **Computed** | 1 if actual > estimated. |
| `approval_delay_hours` | INT | **Computed** | `DATEDIFF(HOUR, purchase, approved)`. |
| `total_payment_value` | DECIMAL(12,2) | NULL | Sum of all payments for the order. |
| `payment_type_primary` | VARCHAR(30) | NULL | First payment method by `payment_sequential`. |
| `payment_installments_max` | INT | NULL | Max installments used. |
| `payment_methods_count` | INT | NULL | Number of distinct payment methods. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `order_sk`  
**Unique constraint:** `uq_fact_orders_order_id` on `order_id`.  
**FKs:** `customer_sk` → `dim_customer`, `order_purchase_date_sk` → `dim_date`.  
**Indexes:** on `customer_unique_id`, `order_status`, `order_purchase_date_sk`, `is_late`.

---

### 3.7. `fact_order_items`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `item_sk` | INT | NOT NULL | Surrogate key (identity). |
| `order_id` | VARCHAR(50) | NOT NULL | FK to `fact_orders`. |
| `order_item_id` | INT | NOT NULL | Line item number. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Denormalised customer key. |
| `product_sk` | INT | NULL | FK to `dim_product`. |
| `seller_sk` | INT | NULL | FK to `dim_seller`. |
| `product_id` | VARCHAR(50) | NULL | Natural product key. |
| `seller_id` | VARCHAR(50) | NULL | Natural seller key. |
| `price` | DECIMAL(12,2) | NOT NULL | Item price (BRL). |
| `freight_value` | DECIMAL(12,2) | NOT NULL | Shipping cost (BRL). |
| `gmv` | DECIMAL(14,2) | **Computed** | `price + freight_value`. |
| `freight_ratio` | DECIMAL(6,4) | **Computed** | `freight_value / (price + freight_value)`. |
| `shipping_limit_date` | DATETIME2(3) | NULL | Shipping deadline. |
| `created_at` | DATETIME2(3) | NOT NULL | Insert timestamp. |

**PK:** `item_sk`  
**Unique constraint:** `uq_fact_order_items` on `(order_id, order_item_id)`.  
**FKs:** `product_sk` → `dim_product`, `seller_sk` → `dim_seller`.  
**Indexes:** on `order_id`, `customer_unique_id`, `product_sk`, `seller_sk`.

---

## 4. Gold Layer — Mart Schema

### 4.1. `refresh_log`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `refresh_id` | INT | NOT NULL | Always 1 (singleton row). |
| `as_of_date` | DATE | NOT NULL | Snapshot date for the mart. |
| `ml_cutoff_date` | DATE | NOT NULL | Date used for ML split (2018‑05‑01). |
| `churn_window_days` | INT | NOT NULL | Churn definition (180). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | When the mart was last refreshed. |

**PK:** `refresh_id`  
**Check constraint:** `refresh_id = 1`.

---

### 4.2. `customer_360`

One row per `customer_unique_id`. The core CRM account record.

| Column | Type | Nullable | Description / Source |
|--------|------|----------|----------------------|
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Business key. |
| `customer_state` | VARCHAR(5) | NULL | Current state. |
| `customer_city` | NVARCHAR(150) | NULL | Current city. |
| `total_orders` | INT | NOT NULL | Number of orders. |
| `total_gmv` | DECIMAL(14,2) | NOT NULL | Sum of GMV across all orders. |
| `avg_order_value` | DECIMAL(12,2) | NOT NULL | `total_gmv / total_orders`. |
| `total_freight_paid` | DECIMAL(14,2) | NOT NULL | Sum of freight paid. |
| `first_order_date` | DATE | NULL | Earliest order date. |
| `last_order_date` | DATE | NULL | Latest order date. |
| `tenure_days` | INT | NULL | `last_order_date - first_order_date`. |
| `days_since_last_order` | INT | NULL | Days since last order vs `@as_of_date`. |
| `avg_review_score` | DECIMAL(4,2) | NULL | Average review score across orders (per‑order average first). |
| `pct_negative_reviews` | DECIMAL(6,4) | NULL | % of orders with avg review score ≤ 2. |
| `avg_delivery_delta_days` | DECIMAL(8,2) | NULL | Average `delivery_delta_days`. |
| `pct_late_deliveries` | DECIMAL(6,4) | NULL | % of delivered orders with `is_late = 1`. |
| `is_churned` | BIT | NOT NULL | 1 if `days_since_last_order > 180`. |
| `customer_health_score` | DECIMAL(6,2) | NOT NULL | 0–100 composite health score (see §6). |
| `health_tier` | VARCHAR(5) | **Computed** | `'High'` if score ≥ 75, `'Medium'` if ≥ 50, else `'Low'`. |
| `churn_probability` | DECIMAL(6,4) | NULL | **Filled by `churn_model.py`.** |
| `clv_predicted_6m` | DECIMAL(14,2) | NULL | **Filled by `clv_model.py`.** |
| `avg_sentiment_score` | DECIMAL(6,4) | NULL | **Filled by `churn_model.py` (backfilled from `sentiment_scores`).** |
| `expected_next_purchase_days` | DECIMAL(10,1) | NULL | **Filled by `next_purchase.py`.** NULL for customers with <2 orders or no defensible median. |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Last update timestamp. |

**PK:** `customer_unique_id`.  
**Indexes:** on `customer_state`, `customer_health_score`, `health_tier`, `is_churned`.

---

### 4.3. `rfm_features`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Business key. |
| `recency_days` | INT | NOT NULL | Days since last order (`@as_of_date`). |
| `frequency` | INT | NOT NULL | `total_orders`. |
| `monetary` | DECIMAL(14,2) | NOT NULL | `total_gmv`. |
| `recency_score` | TINYINT | NOT NULL | NTILE(5) over `recency_days` (5 = most recent). |
| `frequency_score` | TINYINT | NOT NULL | NTILE(5) over `frequency` (5 = most frequent). |
| `monetary_score` | TINYINT | NOT NULL | NTILE(5) over `monetary` (5 = highest spend). |
| `rfm_score` | VARCHAR(3) | NOT NULL | Concatenation of the three scores (e.g., `'555'`). |
| `rfm_segment` | NVARCHAR(30) | NULL | **Filled by `segmentation.py`.** Business segment (e.g., `'Champions'`). |
| `km_cluster` | TINYINT | NULL | **Filled by `segmentation.py`.** K‑means cluster ID (1–7). |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Last update timestamp. |

**PK:** `customer_unique_id`.  
**Indexes:** on `rfm_score`, `rfm_segment`.

---

### 4.4. `clv_features`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Business key. |
| `avg_order_value` | DECIMAL(12,2) | NOT NULL | From `customer_360`. |
| `order_frequency_per_month` | DECIMAL(10,4) | NULL | `total_orders / (tenure_days / 30.0)`; NULL if tenure = 0. |
| `tenure_months` | DECIMAL(10,2) | NULL | `tenure_days / 30.0`. |
| `total_categories_purchased` | INT | NOT NULL | Distinct product categories (English) purchased pre‑cutoff. |
| `avg_review_score` | DECIMAL(4,2) | NULL | From `customer_360`. |
| `avg_delivery_delta` | DECIMAL(8,2) | NULL | From `customer_360` (renamed for ML). |
| `pct_late` | DECIMAL(6,4) | NULL | From `customer_360`. |
| `customer_state` | VARCHAR(5) | NULL | From `customer_360`. |
| `days_since_last_order` | INT | NULL | From `customer_360` (included as ML feature). |
| `preferred_payment_type` | VARCHAR(30) | NULL | Most frequent payment method (tie‑break deterministic). |
| `actual_gmv_post_cutoff` | DECIMAL(14,2) | NOT NULL | **Target variable** – GMV from orders after `@ml_cutoff_date`. |
| `clv_predicted_6m` | DECIMAL(14,2) | NULL | **Filled by `clv_model.py`.** |
| `clv_ci_lower` | DECIMAL(14,2) | NULL | Lower bound of 80% confidence interval. |
| `clv_ci_upper` | DECIMAL(14,2) | NULL | Upper bound of 80% confidence interval. |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Last update timestamp. |

**PK:** `customer_unique_id`.  
**Indexes:** on `customer_state`, `days_since_last_order`.

---

### 4.5. `sentiment_scores`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `review_id` | VARCHAR(50) | NOT NULL | Review identifier. |
| `order_id` | VARCHAR(50) | NOT NULL | Order identifier. |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Customer key. |
| `review_score` | TINYINT | NOT NULL | Original 1–5 score. |
| `review_comment_message` | NVARCHAR(MAX) | NULL | Review body (Portuguese). |
| `review_creation_date` | DATETIME2(3) | NULL | Review creation date. |
| `compound_score` | DECIMAL(6,4) | NULL | **Filled by `sentiment.py`.** LeIA compound score (–1 to 1). |
| `sentiment_label` | VARCHAR(10) | NULL | **Filled by `sentiment.py`.** `'positive'` / `'neutral'` / `'negative'`. |
| `refreshed_at` | DATETIME2(3) | NOT NULL | Last update timestamp. |

**PK:** `review_id`.  
**Index:** on `customer_unique_id`.

---

### 4.6. `crm_action_queue`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `action_id` | INT | NOT NULL | Surrogate key (identity). |
| `customer_unique_id` | VARCHAR(50) | NOT NULL | Customer key. |
| `action_type` | VARCHAR(30) | NOT NULL | One of: `RETENTION_CAMPAIGN`, `REACTIVATION`, `VIP_UPGRADE`, `MONITOR`. |
| `priority` | VARCHAR(10) | NOT NULL | `'HIGH'`, `'MED'`, or `'LOW'`. |
| `churn_probability` | DECIMAL(6,4) | NULL | Copy of model output at time of action generation. |
| `clv_predicted` | DECIMAL(14,2) | NULL | Copy of predicted CLV (stored as `clv_predicted`). |
| `trigger_reason` | NVARCHAR(300) | NOT NULL | Human‑readable explanation of why the action was assigned. |
| `created_at` | DATETIME2(3) | NOT NULL | Row creation timestamp (default `SYSUTCDATETIME()`). |

**PK:** `action_id`.  
**Check constraints:** `action_type IN ('RETENTION_CAMPAIGN','REACTIVATION','VIP_UPGRADE','MONITOR')`, `priority IN ('HIGH','MED','LOW')`.  
**Indexes:** on `customer_unique_id`, on `(priority, action_type)`.

**Note:** This is a current‑state snapshot table. `TRUNCATE` + `INSERT` on each run; history is stored in `action_run_log`.

---

### 4.7. `action_run_log`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `run_id` | INT | NOT NULL | Surrogate key (identity). |
| `run_timestamp` | DATETIME2(3) | NOT NULL | When the run started. |
| `run_by` | NVARCHAR(100) | NULL | OS username. |
| `script_version` | VARCHAR(20) | NULL | Version of `action_rules.py`. |
| `churn_threshold_used` | FLOAT | NOT NULL | Threshold used for churn rules. |
| `clv_percentile_used` | INT | NOT NULL | Percentile used for CLV split (e.g., 50). |
| `vip_percentile_used` | INT | NOT NULL | Threshold for VIP (e.g., 90). |
| `write_mode` | VARCHAR(30) | NOT NULL | `'TRUNCATE_INSERT'` or `'DRY_RUN'`. |
| `n_retention_campaign` | INT | NOT NULL | Count of actions of that type. |
| `n_reactivation` | INT | NOT NULL | |
| `n_vip_upgrade` | INT | NOT NULL | |
| `n_at_risk_nurture` | INT | NOT NULL | (Not currently used, but reserved.) |
| `n_monitor` | INT | NOT NULL | |
| `n_total` | INT | NOT NULL | Total customers processed. |
| `n_priority_high` | INT | NOT NULL | |
| `n_priority_med` | INT | NOT NULL | |
| `n_priority_low` | INT | NOT NULL | |
| `n_customers_in` | INT | NOT NULL | Rows read from `vw_customer_health`. |
| `n_customers_unmatched` | INT | NOT NULL | Should always be 0. |
| `config_snapshot` | NVARCHAR(MAX) | NULL | JSON snapshot of the rule config used. |
| `run_notes` | NVARCHAR(500) | NULL | Additional notes. |

**PK:** `run_id`.  
**Index:** `ix_action_run_log_timestamp` on `run_timestamp DESC`.

---

## 5. Analytical Views

### 5.1. `vw_customer_health`

Flat, denormalized view for Power BI and Python.

**Columns:**
- All columns from `customer_360`
- `recency_band` (0–30d, 31–90d, 91–180d, 180+)
- RFM: `rfm_score`, `rfm_segment`, `km_cluster`, `recency_score`, `frequency_score`, `monetary_score`
- Tiers: `recency_tier`, `frequency_tier`, `monetary_tier` (High/Medium/Low derived from 1–5 scores)
- CLV: `actual_gmv_post_cutoff`, `clv_ci_lower`, `clv_ci_upper`, `preferred_payment_type`, `total_categories_purchased`
- Latest action: `latest_action_type`, `latest_action_priority`, `latest_action_reason`, `latest_action_date`
- `as_of_date` (from `refresh_log`)

**Use:** Single source for Power BI Customer 360 page.

---

### 5.2. `vw_churn_signals`

Filters to customers with `is_churned = 1` or `churn_probability > 0.4`.

**Derived columns:**
- `churn_driver_summary`: rule‑vs‑model agreement.
- `primary_driver`: the most severe dimension (delivery, satisfaction, recency, low value).
- `urgency_score`: 0–100 triage score (see §6).

**Use:** Churn risk page.

---

### 5.3. `vw_geo_performance`

State‑level aggregates.

**Columns:**
- `customer_state`, `customer_count`, `total_gmv`, `pct_of_total_gmv`, `pct_of_total_customers`,
- `avg_delivery_delta_days`, `pct_late_deliveries`, `churn_rate_pct`, `avg_health_score`,
- `dashboard_state_label`: collapses states with <2% GMV into `'Other'`.

**Use:** Geo intelligence dashboard.

---

## 6. Business Definitions & Formulas

| Concept | Definition |
|---------|------------|
| **Churn** | A customer is churned if `days_since_last_order > 180` (anchor date = `2018-10-17`, the max order timestamp). |
| **GMV** | Gross Merchandise Value = `price + freight_value` per order item. |
| **Delivery Delta** | `order_delivered_customer_date - order_estimated_delivery_date`. Negative = early, positive = late. |
| **Health Score** | `(recency_pct × 0.4) + (monetary_pct × 0.4) + (satisfaction_pct × 0.2)` × 100, where each is a percentile rank. |
| **Recency Score (RFM)** | NTILE(5) of `days_since_last_order` in descending order (5 = most recent). |
| **Frequency Score** | NTILE(5) of `total_orders` in descending order (5 = most frequent). |
| **Monetary Score** | NTILE(5) of `total_gmv` in descending order (5 = highest spend). |
| **CLV Target** | `actual_gmv_post_cutoff` = GMV from orders placed on or after `2018-05-01`. |
| **Action Rules** | Evaluated in order: RETENTION_CAMPAIGN (churn ≥ 0.6 & clv > median), REACTIVATION (churn ≥ 0.6 & clv ≤ median), VIP_UPGRADE (Champions & clv > p90), MONITOR (catch‑all). |
| **Urgency Score** | `0.4 × churn_probability + 0.4 × (1 − monetary_badness) + 0.2 × min(days_since_last_order/360, 1)`, where `monetary_badness` is scaled from 0 (high value) to 1 (low value). |

---

## 7. Relationship Diagram (Conceptual)

**Silver Layer:**
```
dim_customer (PK customer_sk)
    └── fact_orders (FK customer_sk)
    └── fact_order_items (denormalised customer_unique_id, no FK)

dim_product (PK product_sk)
    └── fact_order_items (FK product_sk)

dim_seller (PK seller_sk)
    └── fact_order_items (FK seller_sk)

dim_date (PK date_sk)
    └── fact_orders (FK order_purchase_date_sk)

dim_review (no FKs; tied to order_id/customer_unique_id)
```

**Gold Layer (aggregated from Silver):**
```
customer_360 (PK customer_unique_id)
    └── rfm_features (PK customer_unique_id)
    └── clv_features (PK customer_unique_id)
    └── crm_action_queue (FK customer_unique_id)
    └── vw_customer_health (joins all of the above)

sentiment_scores (PK review_id, indexed on customer_unique_id)
```

---

## 8. Guidance for Power BI Developers

### Recommended DAX Measures (Examples)

| Measure | Formula (Conceptual) |
|---------|----------------------|
| **Total GMV** | `SUM(vw_customer_health[total_gmv])` |
| **Avg Churn Probability** | `AVERAGE(vw_customer_health[churn_probability])` |
| **Actionable Customers** | `COUNTROWS(FILTER(vw_customer_health, [latest_action_type] <> "MONITOR"))` |
| **High‑Priority Customers** | `COUNTROWS(FILTER(vw_customer_health, [latest_action_priority] = "HIGH"))` |

### Recommended Dimensions

| Visual | Recommended Dimension |
|--------|-----------------------|
| Segments | `rfm_segment` |
| Health | `health_tier` |
| Recency | `recency_band` |
| Geography | `customer_state` |
| Action Type | `latest_action_type` |

### Important Filters to Apply

- **Churn dashboard:** Filter `is_churned = 1` or use `vw_churn_signals`.
- **Sentiment analysis:** Filter `review_comment_message IS NOT NULL` for NLP visuals.
- **Geo dashboard:** Use `dashboard_state_label` to group small states.

### Performance Recommendations

- Use **Import mode** for dimension tables (`dim_date`, `dim_customer`, `dim_product`).
- Use **DirectQuery** on views (`vw_customer_health`, `vw_churn_signals`, `vw_geo_performance`) if the data volume is large, or Import if you can refresh daily.
- All heavy aggregations (health score, RFM scores, CLV) are pre‑computed in SQL – avoid recreating them in DAX.
- The view `vw_customer_health` already joins `customer_360`, `rfm_features`, `clv_features`, and the latest action, so you can build a single table model without additional joins.

### Handling NULLs

- `avg_sentiment_score`: NULL for customers who never left a review. Interpret as “no opinion”, not neutral.
- `expected_next_purchase_days`: NULL for single‑order customers or when the survival curve never crosses 50%. These customers are not modelled; use `is_churned` for their churn status.
- `clv_predicted_6m`: NULL for customers with no pre‑cutoff order (they are not included in the CLV model). Use `0` as a fallback if needed.

---

**End of Data Dictionary**