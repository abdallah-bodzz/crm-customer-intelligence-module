# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] — 2026-06-30

First complete, production-ready release. All six phases shipped.

### Phase 6 — Power BI Dashboards

**Added**
- Seven-page Power BI report (`CRM_Customer_Intelligence.pbix`)
- Seven-page dark-theme variant (`CRM_Customer_Intelligence - Dark_theme.pbix`)
- 29 DAX measures across KPI, churn, action queue, CLV, sentiment, geo, and What-If domains
- 7 DAX calculated columns (CLV band, churn bucket, quadrant, priority sort)
- What-If parameter for real-time churn threshold simulation (0.2–0.9)
- 14 bookmarks across all pages (metric-switchers, view toggles, panel controls)
- Left-rail navigation system with active-state indicator
- Drill-through to Customer 360 from all pages
- RFM Tooltip page (hover on scatter → segment detail)
- Two complete custom Power BI JSON themes: **Warm Clay** (light) and **Ember** (dark)
- Power BI Implementation Guide (`powerbi/PowerBI_Dashboard_Implementation_Guide.md`)
- Full DAX script (`powerbi/CRM_Intelligence_Measures.dax.md`)
- Power BI blueprint HTML (`powerbi/powerbi_blueprint.html`)
- PDF exports for both themes
- Video walkthrough recording

**Fixed**
- `vw_customer_health` selected `c360.clv_predicted_6m` (dead column, never populated)
  instead of `clv.clv_predicted_6m` — all CLV visuals were blank despite 71,186 predictions
  in `mart.clv_features`. Fixed view alias; added regression check to `07_verify_mart.sql`.

---

## [0.5.0] — 2026-06-26

### Phase 5 — ML Pipeline

**Added**
- `sentiment.py` — LeIA Portuguese sentiment scoring; 40,641 reviews scored
- `segmentation.py` — Exhaustive 9-segment RFM rule engine + K-means (K=7)
  with silhouette-based search; rule exhaustiveness verified by brute-force
  enumeration of all 125 (R,F,M) combinations at startup
- `clv_model.py` — XGBoost CLV regressor with leakage-corrected feature
  construction (features recomputed from warehouse with explicit cutoff filter)
  + quantile regression 80% CI via `objective='reg:quantileerror'`
- `churn_model.py` — XGBoost classifier with F1-tuned decision threshold;
  sentiment backfill from `mart.sentiment_scores` as setup step
- `next_purchase.py` — Weibull AFT survival analysis; interval-grain training
  dataset with correct right-censoring; `predict_median` with `conditional_after`
- `action_rules.py` — JSON-driven CRM rule engine; startup schema validation;
  `mart.action_run_log` audit trail; CLI threshold overrides
- `run.py` — subprocess-isolated pipeline orchestrator; `--all --dry-run
  --force --batch-size --threshold` flags
- `utils.py` — shared logging, DB engine, `batched_update`, `retry_on_db_error`,
  `fetch_refresh_log`, `fetch_df`
- `dq_report.py` — post-pipeline ML quality report → `reports/dq_report.html`
- `text_cleaning.py` — lexicon-safe Portuguese text pre-processing for LeIA
- `action_rules.json` — rule definitions (thresholds, evaluation order, priorities)
- `08_migrate_add_expected_next_purchase_days.sql` — non-destructive `ALTER TABLE`
  migration (column was defined in Phase 4 plan but missing from DDL)
- `09_action_run_log.sql` — audit log DDL for action queue run history
- 6 development notebooks (EDA → RFM → CLV → churn → geo → actions)
- 50+ analysis figures in `reports/figures/`

**Fixed**
- Target leakage in churn model: `days_since_last_order` removed from features
  (it IS the label definition; model had F1=1.0 with it included)
- RFM recency NTILE ordering: `ORDER BY recency_days ASC` → `DESC`
  (Champions showed 100% churn; Lost showed 0% before fix)
- Action queue INSERT: corrected column list to match actual `crm_action_queue` DDL
- `AT_RISK_NURTURE` action type removed (violated CHECK constraint)
- Segmentation runtime: 854s → ~20s via `silhouette_score(sample_size=8000)`
- All-NaN column crash in `lifelines`: explicit fallback to `0.0` with warning
  when median is undefined (triggered when `avg_sentiment_score` is 100% NULL)
- CLV features target leakage: features recomputed from warehouse tables with
  explicit cutoff filter instead of reading from `mart.clv_features`

---

## [0.4.0] — 2026-06-24

### Phase 4 — Gold Layer (Mart)

**Added**
- `mart.customer_360` — unified CRM account record; `customer_health_score`
  computed via `PERCENT_RANK()` percentiles; `health_tier` as persisted
  computed column; population-median imputation for zero-review customers
- `mart.rfm_features` — R/F/M values + NTILE(5) quintile scores
- `mart.clv_features` — ML feature matrix; `actual_gmv_post_cutoff` target
  variable (EDA-locked cutoff 2018-05-01)
- `mart.sentiment_scores` — skeleton table; compound_score/sentiment_label
  Python-populated
- `mart.crm_action_queue` — CRM operational action records
- `mart.refresh_log` — singleton shared clock for all views
- `mart.sp_refresh_mart` — transactional, idempotent Gold ETL procedure
- `vw_customer_health` — flat 360 profile for Power BI
- `vw_churn_signals` — at-risk customers with `urgency_score`, `primary_driver`,
  `churn_driver_summary`
- `vw_geo_performance` — state-level aggregates
- `07_verify_mart.sql` — comprehensive Gold verification script

**Fixed**
- `urgency_score` in `vw_churn_signals`: CLV-available and fallback branches
  had opposite directions for "value at stake" — silently inverted the score
  the moment Phase 5 populated CLV; both branches now consistently
  treat higher value = higher urgency

---

## [0.3.0] — 2026-06-24

### Phase 3 — Silver Layer (Warehouse Build)

**Added**
- `warehouse.dim_date` — 2015–2020 + 1900-01-01 sentinel; Brazilian fixed-date
  holidays; fiscal year/quarter columns
- `warehouse.dim_customer` — SCD Type 2; filtered unique index on current rows;
  tracks city/state/zip changes
- `warehouse.dim_product` — Type 1; English category translation; `UNKNOWN`
  for nulls; `product_volume_cm3` persisted
- `warehouse.dim_seller` — Type 1
- `warehouse.dim_review` — deduplicated (Olist duplicate `review_id` quirk);
  `has_comment` flag; `response_delay_days` persisted
- `warehouse.fact_orders` — priority-ranked `CROSS APPLY` point-in-time SCD2
  join; `delivery_delta_days`, `is_late`, `approval_delay_hours` persisted;
  payment aggregates via `OUTER APPLY`
- `warehouse.fact_order_items` — `gmv`, `freight_ratio` persisted;
  `customer_unique_id` denormalized for fast rollups
- `warehouse.sp_load_warehouse` — transactional ETL procedure; idempotent
- `09_verify_silver.sql` — Silver verification script

**Fixed**
- `TRUNCATE` blocked by FK constraints on `dim_product`/`dim_seller`:
  switched to `DELETE` for FK-targeted dimensions
- `dim_review` duplicate key violation: `ROW_NUMBER()` dedupe by latest
  `review_creation_date` + `order_id ASC`

---

## [0.2.0] — 2026-06-23

### Phase 2 — EDA & Bronze Validation

**Added**
- `notebooks/01_eda.ipynb` — full exploratory analysis
- `reports/eda_summary.json` — single source of truth for all downstream ML decisions
- 10 EDA figures in `reports/figures/`

**Decisions locked**
- ML cutoff: `2018-05-01` (replaced `2018-09-01` which left ~0 test rows)
- Churn window: 180 days
- SCD2 justified: 3.12% of customers have >1 `customer_id`
- Sentiment tool: LeIA (VADER English-only lexicon silently mis-scores PT-BR)
- `customer_state` required as churn feature (regional delivery variance confirmed)

---

## [0.1.0] — 2026-06-22

### Phase 1 — Bronze Ingestion

**Added**
- `ingest_bronze.py` — chunked CSV ingestion with per-file dtype maps, audit
  columns, exponential-backoff retry, and SQL Server parameter-limit-aware
  chunk sizing
- `sql/00_setup/00_setup_bronze_staging.sql` — database and staging schema DDL
- `config.py` — dotenv-based DB connection
- `.env.example` — credential template
- `requirements.txt` — pinned Python dependencies
- All 9 Olist CSV files validated and loaded into `staging` schema

---

[1.0.0]: https://github.com/abdallah-bodzz/crm-customer-intelligence-module/releases/tag/v1.0.0
[0.5.0]: https://github.com/abdallah-bodzz/crm-customer-intelligence-module/releases/tag/v0.5.0
[0.4.0]: https://github.com/abdallah-bodzz/crm-customer-intelligence-module/releases/tag/v0.4.0
[0.3.0]: https://github.com/abdallah-bodzz/crm-customer-intelligence-module/releases/tag/v0.3.0
[0.2.0]: https://github.com/abdallah-bodzz/crm-customer-intelligence-module/releases/tag/v0.2.0
[0.1.0]: https://github.com/abdallah-bodzz/crm-customer-intelligence-module/releases/tag/v0.1.0
