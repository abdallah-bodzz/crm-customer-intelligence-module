## Phase 2 Completion Report – EDA & Bronze Validation

**Project:** CRM Customer Intelligence Module  
**Phase:** 2 – Exploratory Data Analysis  
**Status:** ✅ COMPLETE  
**Date:** 2026-06-23  
**Artifacts:** `notebooks/01_eda.ipynb`, `reports/eda_summary.json`, 10 figures

---

## Executive Summary

The Bronze layer (1.55M rows across 9 staging tables) has been validated against documented Kaggle expectations. The customer identity problem is quantified: **3.12% of customers** have multiple `customer_id` values – SCD Type 2 is justified by evidence, not assumption.

The ML cutoff is **locked at `2018-05-01`**, providing a **valid temporal split** (~93% train, ~7% test) with 4+ months of post-cutoff data for CLV validation. This replaces the original `2018-09-01` cutoff which had effectively zero test data.

**Key structural insight:** 96.88% of customers have only one order. Churn is primarily structural (one-time buyers), not behavioral – `churn_model.py` must use `scale_pos_weight`, and the Power BI churn dashboard should default to `order_count >= 2`.

**VADER sentiment** ships: 41.29% of reviews contain text, with coverage skewed toward low scores (76% of 1-star reviews have text) – exactly the population a churn model needs to understand.

**Regional variance** in delivery performance justifies both a Brazilian holiday calendar in `dim_date` and `customer_state` as a required churn feature.

All decisions are exported to `eda_summary.json` – the single source of truth consumed by all downstream ML scripts.

---

## Data Quality Validation

| Check | Result |
|-------|--------|
| Bronze row counts | ✅ All 9 tables match expected counts |
| Critical FK nulls | ✅ 0 nulls on all join keys |
| Soft nulls (delivery date) | 2.98% – expected (cancelled/undelivered orders) |
| Soft nulls (review text) | 58.71% – expected; VADER runs only on non-empty |
| Soft nulls (product category) | 1.85% – map to 'UNKNOWN' in Silver |

---

## Key Decisions (Locked for Downstream)

| Domain | Decision | Evidence |
|--------|----------|----------|
| **Customer identity** | SCD Type 2 on `customer_unique_id` | 3.12% customers have >1 `customer_id` |
| **ML cutoff** | `2018-05-01` | ~93% train, ~7% test with 4+ months validation |
| **Churn definition** | No order in 180 days | 71.18% baseline churn (structural) |
| **VADER sentiment** | Include, scope to non-empty text | 41.29% coverage, skewed to low scores |
| **Delivery** | `customer_state` = required feature | Regional variance: -8 to -20 days avg delta |
| **Geo dashboard** | Collapse states <2% GMV into "Other" | Top 5 states = 77% customers, 74% GMV |
| **VIP segment** | `VIP_UPGRADE` action type justified | Top 20% customers = 56.8% of GMV |
| **Repeat buyer AOV** | Retention = frequency, not basket | Repeat buyers spend R$124 vs R$138 AOV |
| **Product category nulls** | Map to 'UNKNOWN' in Silver | 1.85% null rate |

---

## Temporal Split (Post-Fix)

```
TRAIN: 92,812 orders (92.64%)
TEST:  6,607 orders (7.36%)
Cutoff: 2018-05-01
Post-cutoff validation window: May–Oct 2018 (4+ months)
```

---

## Feature Set Carried Forward

The following features are validated for multicollinearity and mapped to `mart.customer_360` columns:

| Feature | Source | Correlation |
|---------|--------|-------------|
| `order_count` | `stg_orders` | — |
| `total_gmv` | `stg_order_items` | 0.91 with avg_item_price (expected) |
| `avg_review_score` | `stg_order_reviews` | — |
| `avg_delivery_delta` | `stg_orders` | 0.61 with pct_late (expected) |
| `pct_late` | `stg_orders` | — |

No features are dropped at this stage – model-specific feature selection occurs in individual ML scripts.

---

## Artifacts Generated

| Artifact | Location | Purpose |
|----------|----------|---------|
| `01_eda.ipynb` | `notebooks/` | Executed notebook with all outputs |
| `eda_summary.json` | `reports/` | Single source of truth for ML models |
| `01_customer_identity_multiplicity.png` | `reports/figures/` | SCD2 evidence |
| `02_monthly_volume_gmv.png` | `reports/figures/` | Temporal + cutoff visual |
| `03_review_score_and_text_rate.png` | `reports/figures/` | VADER coverage |
| `04_score_vs_delivery_delay.png` | `reports/figures/` | Delivery → review correlation |
| `05_delivery_performance_by_state.png` | `reports/figures/` | Regional variance |
| `06_orders_per_customer.png` | `reports/figures/` | One-time buyer dominance |
| `07_payment_methods_and_installments.png` | `reports/figures/` | Payment distribution |
| `08_gmv_pareto_curve.png` | `reports/figures/` | GMV concentration |
| `09_geo_gmv_and_delivery_risk.png` | `reports/figures/` | Geo + delivery intersection |
| `10_feature_correlation_matrix.png` | `reports/figures/` | Multicollinearity check |

---

## Next Milestone

**Phase 3 – Silver Layer (Warehouse Build):**
- `warehouse.dim_customer` (SCD2 logic)
- `warehouse.fact_orders` (delivery_delta, is_late)
- `warehouse.fact_order_items` (GMV, freight_ratio)
- `warehouse.dim_review`
- `warehouse.dim_date` (with Brazilian holiday calendar)
- `warehouse.sp_load_warehouse` (Bronze → Silver ETL)

**Prerequisites:** `eda_summary.json` must be present in `reports/` – all Silver scripts can reference it for decisions (e.g., `ml_cutoff_date`, `churn_definition_days`).

---

## Sign-off

The EDA phase is complete. Bronze is validated. Decisions are locked and exported. The pipeline is ready for Silver.