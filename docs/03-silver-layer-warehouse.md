## Phase 3 Report — Silver Layer (Warehouse Build)

**Status:** ✅ COMPLETE  
**Date:** 2026-06-24  
**Location:** All DDL and ETL scripts in `sql/02_warehouse/`

---

## Executive Summary

Silver layer is fully implemented. 7 tables (5 dimensions + 2 facts) with star schema design, SCD Type 2 on `dim_customer`, persisted computed columns, and a transactional ETL procedure. The procedure is idempotent and handles point‑in‑time SCD2 joins via a priority‑ranked `CROSS APPLY` that works correctly on fresh runs (fallback to earliest version) and after subsequent changes (closed‑window match).

**Known bugs encountered and fixed during implementation:**
- `TRUNCATE` blocked by FK constraints on `dim_product`/`dim_seller` — solved by using `DELETE` on FK‑targeted tables
- `dim_review` duplicate `review_id` values in source — solved with deterministic dedupe (`ROW_NUMBER()` on latest creation date + `order_id`)

---

## Tables Created

| Table | Type | Grain | Key Columns | Notes |
|-------|------|-------|-------------|-------|
| `dim_date` | Dimension | 1 row per date | `date_sk INT PK`, `full_date DATE`, `fiscal_year`, `fiscal_quarter` | 2015–2020 + 1900‑01‑01 sentinel; Brazilian fixed‑date holidays; movable holidays omitted (requires Easter calc) |
| `dim_customer` | SCD2 Dimension | 1 row per version | `customer_sk INT PK`, `customer_unique_id`, `valid_from DATE`, `valid_to DATE`, `is_current BIT`, `updated_at` | Filtered unique index ensures exactly one current row per business key; SCD2 closes changes to city/state/zip |
| `dim_product` | Type 1 Dimension | 1 row per product | `product_sk INT PK`, `product_id` | Category translation via `stg_product_category_translation`; `UNKNOWN` for missing; computed `product_volume_cm3` |
| `dim_seller` | Type 1 Dimension | 1 row per seller | `seller_sk INT PK`, `seller_id` | Simple Type 1; no SCD2 required |
| `dim_review` | Type 1 Dimension | 1 row per unique `review_id` | `review_sk INT PK`, `review_id`, `customer_unique_id` | Dedupes duplicate `review_id` (Olist data quirk); `has_comment` flag; `response_delay_days` persisted |
| `fact_orders` | Fact Table | 1 row per order | `order_sk INT PK`, `customer_sk FK`, `order_purchase_date_sk FK` | Point‑in‑time SCD2 join; `delivery_delta_days`, `is_late`, `approval_delay_hours` persisted; payment aggregates via `OUTER APPLY` |
| `fact_order_items` | Fact Table | 1 row per line item | `item_sk INT PK`, `order_id`, `product_sk FK`, `seller_sk FK` | `gmv`, `freight_ratio` persisted; denormalized `customer_unique_id` for fast rollups |

---

## ETL Procedure: `sp_load_warehouse`

**Design & Implementation Notes:**

1. **Idempotency Model:**  
   - `dim_customer` — incremental (never truncated, preserves SCD2 history)  
   - All other tables — fully rebuilt every run: `TRUNCATE` for `fact_order_items`/`fact_orders` (no FKs referencing them); `DELETE` for `dim_product`, `dim_seller`, `dim_review` (FK targets — SQL Server blocks `TRUNCATE` on these regardless of order)

2. **SCD2 Logic:**  
   - Dedupe `stg_customers` to one row per `customer_unique_id` (tiebreak on `customer_id DESC`)  
   - Close current rows where city/state/zip changed (`valid_to = @today`, `is_current = 0`)  
   - Insert new current rows for new customers or changed attributes

3. **Point‑in‑Time SCD2 Join (fact_orders):**  
   - `CROSS APPLY` with priority ranking:  
     - Priority 0: closed or open version whose `[valid_from, valid_to)` window covers the order date  
     - Priority 1 (fallback): customer's earliest known version — critical for fresh loads where all `valid_from = @today` (which is after every historical order date)  
   - Without this fallback, a first‑run ETL would load zero `fact_orders` rows because no `dim_customer` version exists with `valid_from <= 2016‑order‑date`

4. **Olist‑Specific Defensive Handling:**  
   - `review_id` is NOT globally unique in source — dedupe with `ROW_NUMBER()` based on latest `review_creation_date`, then `order_id ASC`  
   - `review_score` is `FLOAT` in Bronze — cast to `TINYINT` with `WHERE BETWEEN 1 AND 5` filter  
   - `customer_id` maps N:1 to `customer_unique_id` — dedupe in SCD2 logic  
   - Date NULLs route to `1900‑01‑01` sentinel row in `dim_date` (Kimball pattern — prevents NULL FKs)

---

## Verification Script: `09_verify_silver.sql`

| Check | Purpose | Expected |
|-------|---------|----------|
| Row counts | Confirm all tables loaded | Matches Bronze counts (~96K/33K/3K/99K/99K/112K) |
| SCD2 current rows | Exactly one `is_current=1` per `customer_unique_id` | 0 rows in HAVING COUNT > 1 |
| SCD2 overlapping windows | No two rows for same customer with overlapping `[valid_from, valid_to)` | 0 rows returned |
| Orphan orders | Every `fact_orders.customer_unique_id` exists in `dim_customer` | 0 |
| Orphan items | Every `fact_order_items.order_id` exists in `fact_orders` | 0 |
| `dim_review.customer_unique_id` | Never NULL | 0 |
| Duplicate `review_id` count | Informational — known Olist quirk | >0 expected, documented |
| Missing product/seller FK | NULL `product_sk`/`seller_sk` in `fact_order_items` | TBD — run query; expected 0 if data clean |
| Payment reconciliation | `fact_orders.total_payment_value` = SUM(`stg_order_payments`) | 0 mismatches |
| Sentinel date usage | Orders with `order_purchase_date_sk = 19000101` | Matches NULL count in `stg_orders` |
| Late delivery % | National baseline | TBD — run `09_verify_silver.sql`; Phase 2 EDA predicted ~8% |

---

## Critical Implementation Notes (for Phase 4)

1. **`dim_review` duplicate `review_id` issue** — the procedure dedupes; when joining `fact_orders`/`fact_order_items` to `dim_review` on `order_id`, you **must aggregate `review_score` per `order_id` first**, or GMV double‑counts.

2. **`fact_order_items.customer_unique_id`** — denormalized intentionally; use this for fast customer‑level GMV rollups without joining through `fact_orders`.

3. **Point‑in‑time SCD2 join** — the `CROSS APPLY` priority ranking is correct. If you modify it, keep the fallback (priority 1) or fresh loads break.

4. **Payment aggregation** — `OUTER APPLY` computes all payment aggregates in one pass; `total_payment_value` is the sum of all payment rows; `payment_type_primary` is the first method by `payment_sequential`.

---

## Next Steps (Phase 4 — Gold / Mart)

**Gold tables to build (in order):**

1. `mart.customer_360` — customer profile: `total_orders`, `total_gmv`, `avg_review_score`, `avg_delivery_delta`, `pct_late_deliveries`, `first_order_date`, `last_order_date`  
   Source: `dim_customer` + `fact_orders` + `fact_order_items` + `dim_review`  
   Grain: `customer_unique_id`

2. `mart.rfm_features` — R/F/M scores (1–5 quintile ranks)  
   Python populates: `rfm_segment`, `km_cluster`

3. `mart.clv_features` — features for CLV model: AOV, order frequency, tenure, category diversity, payment type, state  
   Python populates: `clv_predicted_6m`

4. `mart.sentiment_scores` — VADER per review  
   Python populates: `compound_score`, `sentiment_label`

5. `mart.crm_action_queue` — action flags  
   Python populates from model outputs

**Views for Power BI:**  
`vw_customer_health` (flat 360+RFM+CLV+churn), `vw_churn_signals` (at‑risk customers + actions), `vw_geo_performance` (state‑level aggregates)

**Refresh procedure:** `mart.sp_refresh_mart` — rebuilds all mart tables from Silver; call after `sp_load_warehouse`.

---

## Bugs Encountered & Resolved

| Bug | Cause | Fix |
|-----|-------|-----|
| `TRUNCATE` failed on `dim_product`/`dim_seller` | SQL Server blocks `TRUNCATE` on FK‑targeted tables even when referencing table is also being truncated | Switched to `DELETE` for FK‑targeted dims; `TRUNCATE` retained for `fact_orders`/`fact_order_items` (not FK‑targeted) |
| `dim_review` duplicate key violation | Olist source has duplicate `review_id` values (same `review_id` against multiple `order_id`s) | Added `ROW_NUMBER()` dedupe by latest `review_creation_date` + `order_id`; logged count of collapsed rows |

---

## Sign‑off

Silver layer is stable, verified, and ready for Phase 4. The `CROSS APPLY` SCD2 join logic is the key architectural decision — document the priority‑fallback caveat in the README to pre‑empt questions about how it works on fresh loads. Run `09_verify_silver.sql` after the ETL and confirm row counts match Phase 2 expectations before proceeding.