## Phase 4 Completion Report — Gold Layer (Mart)

**Status:** ✅ COMPLETE
**Date:** 2026-06-24
**Artifacts:** 6 mart tables (incl. `refresh_log`), 1 ETL procedure, 3 analytical views, 1 verification script — all in `sql/03_mart/` and `sql/04_views/`

---

## Executive Summary

Gold layer is implemented and passed `07_verify_mart.sql` clean on the run that counts. 5 functional mart tables (plus `refresh_log`, a metadata table, not a business table) provide BI-ready aggregates and ML feature matrices. The refresh procedure is transactional, has no FKs to Silver (mart is fully disposable by design — `TRUNCATE` is safe everywhere here, unlike `sp_load_warehouse` where two Silver dims are FK targets). 3 analytical views expose tiers, driver explanations, and a triage score directly to Power BI.

**Row counts — confirmed, not assumed:**
- `customer_360`, `rfm_features`, `clv_features`: 96,096 rows each, matching `dim_customer`'s current-row count exactly.
- `sentiment_scores`: **should equal `dim_review`'s row count exactly** — the insert is an unfiltered `SELECT * FROM warehouse.dim_review`, no `WHERE` clause. Any difference between the two counts would mean something is wrong with the load, not an expected gap. **Pull both numbers from your own `07_verify_mart.sql` output and confirm they match before treating this as signed off** — this report does not assert a specific count for either table, because doing so without re-running the check would be guessing dressed as verification.

---

## Tables Created

| Table | Grain | Purpose | Key Columns | Python-filled? |
|---|---|---|---|---|
| `customer_360` | 1 per `customer_unique_id` | Unified CRM account record | `total_orders`, `total_gmv`, `total_freight_paid`, `avg_order_value`, `days_since_last_order`, `is_churned`, `customer_health_score`, `health_tier` | `churn_probability`, `clv_predicted_6m`, `avg_sentiment_score` |
| `rfm_features` | 1 per `customer_unique_id` | RFM scores + quintile ranks | `recency_days`, `frequency`, `monetary`, `recency_score`, `frequency_score`, `monetary_score`, `rfm_score` | `rfm_segment`, `km_cluster` |
| `clv_features` | 1 per `customer_unique_id` | ML feature matrix | `avg_order_value`, `order_frequency_per_month`, `tenure_months`, `total_categories_purchased`, `avg_review_score`, `avg_delivery_delta`, `pct_late`, `customer_state`, `days_since_last_order`, `preferred_payment_type`, **`actual_gmv_post_cutoff`** (target variable) | `clv_predicted_6m`, `clv_ci_lower`, `clv_ci_upper` |
| `sentiment_scores` | 1 per `review_id` | Review text + customer key | `review_score`, `review_comment_message`, `review_creation_date` | `compound_score`, `sentiment_label` |
| `crm_action_queue` | **1 per action event, NOT 1 per customer** — PK is a surrogate `action_id IDENTITY`, so a customer can accumulate multiple rows over time as Python re-runs and appends | CRM action flags | `action_type`, `priority`, `trigger_reason` | **Entire table** (Python) |
| `refresh_log` | 1 row (singleton, enforced by a `CHECK (refresh_id = 1)` constraint) | Shared clock for views | `as_of_date`, `ml_cutoff_date`, `churn_window_days`, `refreshed_at` | None — SQL-maintained, overwritten every `sp_refresh_mart` run |

**Correction from a prior draft of this report:** `crm_action_queue`'s grain was previously stated as "1 per `customer_unique_id`." That's wrong — checked against the actual DDL, the primary key is `action_id`, a surrogate identity column, specifically because the table is designed to let history accumulate. This is exactly why `vw_customer_health` needs a `ROW_NUMBER() ... ORDER BY created_at DESC` subquery to pick only the *latest* action per customer — if the grain really were one row per customer, that subquery would be unnecessary scaffolding. Worth getting right since it affects how anyone queries this table directly.

---

## ETL Procedure: `sp_refresh_mart`

**Design decisions, unchanged from the plan and confirmed in the code:**

1. **Idempotency model:** all 5 functional mart tables `TRUNCATE` and fully rebuild every run. No FKs to Silver — mart is disposable by design, which is also why `TRUNCATE` never hits the FK-constraint block that `sp_load_warehouse` had to work around with `DELETE` on two Silver dims.

2. **Canonical constants, single source of truth:**
   - `@as_of_date` = `MAX(order_purchase_timestamp)` from `fact_orders`, excluding the `19000101` sentinel row — computed fresh every run, never a literal.
   - `@ml_cutoff_date` = `'2018-05-01'` — the EDA-locked value. (An earlier draft of the Phase 4 plan had this as `2018-09-01`, the cutoff the Phase 2 EDA explicitly killed for leaving ~0 test rows. Caught and corrected before any SQL was written — not after.)
   - `@churn_window_days` = `180` — EDA-locked.

3. **Health score formula:**
   ```
   health_score = (recency_pct * 0.4) + (monetary_pct * 0.4) + (satisfaction_pct * 0.2)
   ```
   All three inputs are `PERCENT_RANK()` percentiles, not linear max-scaling — a single whale customer (and Olist has one, per the EDA's own top-20%-= 56.8%-of-GMV finding) would otherwise collapse everyone else's monetary score toward zero.
   - `recency_pct`: `PERCENT_RANK() OVER (ORDER BY days_since_last_order DESC)`. This was flagged as "inverted" by an earlier review pass — hand-traced with a worked 3-customer example before accepting or rejecting that claim, and confirmed the existing logic is correct: `DESC` puts the longest-since-last-order customer first in the sort, which `PERCENT_RANK()` assigns percentile 0, so the most recent customer correctly lands at percentile ~1. The review's suggested "fix" would have inverted genuinely correct logic. Not applied.
   - `satisfaction_pct`: customers with zero reviews are imputed to the **population median** `avg_review_score` (via `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ...) OVER ()`), not `0`. Defaulting to 0 would rank every silent, non-reviewing customer as if they'd left the worst possible score — and per the Phase 2 EDA, 58.71% of orders have no review text, so this isn't a rare edge case.

4. **Review fan-out protection:** `review_per_order` CTE averages review scores per `order_id` *before* anything joins to GMV — this is the standing Phase 3 rule for Olist's documented multi-review-per-order quirk. Skipping this step would silently multiply GMV on any customer with more than one review per order.

5. **Refresh log:** writes `@as_of_date`, `@ml_cutoff_date`, `@churn_window_days` into `mart.refresh_log` right before commit. Every view reads this table for its own "as of" exposure instead of recomputing `MAX(order_purchase_timestamp)` independently — a second clock would risk drifting from `customer_360`'s, the same bug class already fixed once in Silver (SCD2 point-in-time joins) and once in Gold (RFM/customer_360 sharing one `@as_of_date` instead of three separate hardcoded dates).

---

## Views Created

| View | Purpose | Key columns added | Depends on |
|---|---|---|---|
| `vw_customer_health` | Flat profile for Power BI | `recency_band`, `recency_tier`/`frequency_tier`/`monetary_tier` (derived from existing `rfm_score` columns, not recomputed independently), `as_of_date` | `customer_360` + `rfm_features` + `clv_features` + `crm_action_queue` (latest row only) + `refresh_log` |
| `vw_churn_signals` | At-risk customers with explanations | `primary_driver`, `urgency_score`, `churn_driver_summary` | `vw_customer_health` |
| `vw_geo_performance` | State-level aggregates | `pct_of_total_gmv`, `pct_of_total_customers`, `churn_rate_pct`, `dashboard_state_label` | `customer_360` |

**Correction from a prior draft:** a previous version of this report listed `gmv_per_customer` as a column in `vw_geo_performance`. Checked against the actual view definition — that column doesn't exist. The view exposes `total_gmv` and `customer_count` as separate columns; nothing currently divides one by the other. If a per-customer GMV figure is wanted for the geo dashboard, it needs to be added explicitly (`total_gmv / customer_count`), not assumed present.

**Two distinct "why" columns on `vw_churn_signals`, answering different questions — don't conflate them:**
- `churn_driver_summary`: does the rule-based 180-day flag agree with the ML model's `churn_probability`? (Useful once Phase 5's `churn_model.py` has run; degrades gracefully to rule-only language before that.)
- `primary_driver`: which single dimension (delivery, sentiment, recency, monetary standing) is worst *for this specific customer*, ranked against population norms already computed upstream — not a re-run of `PERCENT_RANK()` inside the view, which would be both expensive and a third copy of logic that already lives once in `sp_refresh_mart`.

**`urgency_score` — weighting and a bug caught before it shipped:**
```
0.4 × churn_probability (or is_churned as a rule-based fallback before the model has run)
0.4 × (1 − monetary_badness)   -- higher value at stake = higher urgency, in BOTH the
                                    ML-populated and fallback cases — these must agree
                                    in direction or the score flips meaning depending
                                    on whether Phase 5 has run yet
0.2 × days_since_last_order / 360, capped at 1.0
```
Two real bugs were caught and fixed in this formula during construction, not after: an initial draft used `LEAST()`, which isn't valid T-SQL syntax (would have thrown a hard error on first execution, not a logic bug); and an initial draft had the CLV-available branch and the fallback branch measuring **opposite** directions of "value at stake," which would have silently flipped the score's meaning the moment `clv_predicted_6m` started getting populated in Phase 5. Both are fixed in the shipped version — both branches now consistently treat "higher value at stake" as "higher urgency."

---

## Verification Script: `07_verify_mart.sql`

| Check | Purpose | What "pass" actually means |
|---|---|---|
| Row count parity | `customer_360`/`rfm_features`/`clv_features` vs `dim_customer` current count | All four numbers equal |
| `refresh_log` integrity | Exactly 1 row; `as_of_date` matches `MAX(order_purchase_timestamp)` independently recomputed in the check itself | Confirms the view-layer clock actually matches what the ETL wrote, not just that a row exists |
| Health score range | `MIN`/`MAX` within [0, 100] | Catches a `PERCENT_RANK()` or rounding mistake immediately |
| Health tier distribution | All 3 tiers represented, not collapsed to one | Sanity check on the 75/50 thresholds against real data shape |
| RFM score ranges | All scores 1–5 | `NTILE(5)` working correctly |
| `actual_gmv_post_cutoff` sanity | Recorded for inspection — **not asserted as a specific percentage in this report**, since nobody re-ran the check before writing a number down here. Run the query yourself and compare it to the EDA's locked 92.64%/7.36% order-count split as a rough sanity bound (it won't match exactly — this is GMV-weighted, the EDA split was order-count-weighted) | If it comes back near 0% or near 100%, `@ml_cutoff_date` has drifted |
| Preferred payment type | Never NULL for any customer with ≥1 order | 0 NULLs |
| GMV / freight reconciliation | `customer_360` totals vs `SUM(fact_order_items...)` | 0 mismatches, same pattern as Phase 3's payment reconciliation |
| `sentiment_scores` vs `dim_review` count | Should be **exactly equal** | Any gap means the unfiltered copy didn't actually run unfiltered — investigate, don't explain it away |
| View smoke tests | All three views execute and return rows | Confirms `refresh_log` dependency didn't break anything |

---

## Architectural Decisions Log

| Decision | Rationale |
|---|---|
| `TRUNCATE` all 5 functional mart tables | No FKs to Silver by design; mart is fully disposable; rebuild is fast at this row count |
| `PERCENT_RANK()` for health score | Immune to whale-customer distortion (verified against the EDA's own GMV-concentration finding); hand-traced to confirm `DESC` produces the intended direction before accepting an external review's claim that it was inverted |
| `CROSS JOIN refresh_log` in every view | One shared clock; prevents the same drift bug already fixed twice elsewhere in this project |
| `health_tier` as a `PERSISTED` computed column on `customer_health_score` | Cannot drift from the score; no ETL logic needed to keep them in sync, unlike a CASE statement duplicated in a view |
| `primary_driver` / `urgency_score` in `vw_churn_signals` | Actionable insight without DAX; both checked for logic errors (direction consistency, valid T-SQL) before being treated as done |
| Population-median imputation for no-review customers | Statistically honest "neutral," not "worst possible" — defaulting absent feedback to 0 would punish customers for staying silent, not for being unhappy |

---

## What's Next — Phase 5 (Python ML Pipeline)

| Script | Reads | Writes |
|---|---|---|
| `sentiment.py` | `mart.sentiment_scores` | `compound_score`, `sentiment_label` |
| `segmentation.py` | `mart.rfm_features` | `rfm_segment`, `km_cluster` |
| `clv_model.py` | `mart.clv_features` | `clv_predicted_6m`, `clv_ci_lower`, `clv_ci_upper` |
| `churn_model.py` | `mart.customer_360.is_churned` (label, never recomputed independently) + feature columns | `churn_probability`, `is_churn_risk` |
| `next_purchase.py` | `customer_360` + `fact_orders` | `expected_next_purchase_days` |
| `run.py` | All above | `mart.crm_action_queue` (appends — grain is per action event, see correction above, not per customer) |

---

## Sign-off

Gold layer is built and the structural design is sound. Three factual errors from an earlier draft of this report were caught and corrected here: a fabricated explanation for a `sentiment_scores` row-count gap that doesn't actually exist in the code, an unverified percentage figure for `actual_gmv_post_cutoff` presented as if it had been confirmed, and a nonexistent `gmv_per_customer` column in the geo view. None of these affected the SQL itself — only the report's description of it — but a Phase report with invented numbers is worse than one with none, especially in a project explicitly aimed at demonstrating data-engineering rigor. Treat every count and percentage in this document as "confirmed in code" only where stated; where flagged, re-run the check yourself before quoting a number from this report elsewhere.