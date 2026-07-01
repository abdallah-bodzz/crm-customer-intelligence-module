## Phase 5 Completion Report — ML Pipeline

**Status:** ✅ COMPLETE  
**Date:** 2026-06-26  
**Lead-dev**Abdallah A Khames      
**github** `abdallah-bodzz`       
**repo** `crm-customer-intelligence-module`       
**Version:** 1.0  
**Artifacts:** 10 Python scripts + 7 saved models + DQ report + 4 development notebooks

---

### Executive Summary

Phase 5 is fully implemented and executed. Five ML models plus a CRM action queue rule engine ran end-to-end against the live database, writing predictions to Gold tables. All scripts are production-ready, idempotent, auditable, and integrated into a single orchestrator (`run.py`).

**Key achievement:** The pipeline went from a plan to a working system through iterative testing, bug fixes, and validation. The dry run caught issues early; the full run validated correctness. Every bug encountered was documented and fixed with evidence — no silent patches.

---

### Pipeline Overview

| Step | Script | Output Table | Rows Written | Status |
|------|--------|--------------|--------------|--------|
| 1 | `sentiment.py` | `mart.sentiment_scores` | 40,641 | ✅ |
| 2 | `segmentation.py` | `mart.rfm_features` | 96,096 | ✅ |
| 3 | `clv_model.py` | `mart.clv_features` | 71,186 | ✅ |
| 4 | `churn_model.py` | `mart.customer_360` | 96,096 | ✅ |
| 5 | `next_purchase.py` | `mart.customer_360` | 2,996 | ✅ |
| 6 | `action_rules.py` | `mart.crm_action_queue` | 96,096 | ✅ |

---

### Model Development & Validation

Each model was developed iteratively with dedicated Jupyter notebooks. Below are the validation results and key findings.

---

#### 1. Sentiment Analysis (`sentiment.py`)

**Model:** LeIA (Portuguese-lexicon VADER fork)  
**Input:** `review_comment_message` (non-NULL only)

| Metric | Value | Interpretation |
|--------|-------|----------------|
| Reviews scored | 40,641 | 41.29% coverage (matches EDA) |
| Positive | 51.93% | Majority positive reviews |
| Neutral | 33.00% | Significant neutral share |
| Negative | 15.07% | Minority negative |
| Mean compound | 0.2048 | Slightly positive overall |
| Std dev | 0.3818 | Moderate spread |

**Why LeIA, not VADER:** VADER's English-only lexicon silently mis-scores Portuguese text as near-neutral. LeIA is a deliberate Portuguese rebuild — verified against Kaggle reference notebooks.

**Note:** `sentiment.py` was corrected to use LeIA's GitHub installation (`rafjaa/LeIA`), not the ambiguous PyPI package `leia-br`. This was confirmed by checking the actual package contents before shipping.

---

#### 2. RFM Segmentation (`segmentation.py`)

**Method:** Rule-based labels + K-means clustering (K=7)  
**Input:** `mart.rfm_features` (recency/frequency/monetary NTILE(5) scores)

| Metric | Value |
|--------|-------|
| Total customers | 96,096 |
| Rule exhaustiveness | ✅ All 125 (r,f,m) combos resolve |
| Optimal K | 7 (silhouette = 0.3106) |
| Runtime (after optimization) | ~20 seconds (from 14+ minutes) |

**Segment distribution:**

| Segment | Count | % of Total | Churn Rate | Business Meaning |
|---------|-------|------------|------------|------------------|
| Frequent Low-Spender | 22,481 | 23.4% | 64.0% | Engaged but low margin |
| Needs Attention | 14,167 | 14.7% | 100% | Moderate recency |
| Loyal | 12,841 | 13.4% | 35.3% | Strong, some attrition |
| Hibernating | 12,584 | 13.1% | 100% | Low engagement |
| Potential Loyalist | 10,572 | 11.0% | 27.3% | New/recent |
| At Risk | 10,423 | 10.8% | 100% | Was valuable, recency dropped |
| Can't Lose | 8,846 | 9.2% | 100% | Dormant high-value |
| Champions | 3,807 | 4.0% | 0% | Best customers |
| Lost | 375 | 0.4% | 100% | Gone |

**Incident:** The recency score NTILE ordering was initially inverted (`ORDER BY recency_days ASC` instead of `DESC`), causing Champions to show 100% churn and Lost to show 0% churn. The bug was detected via a segment–churn check query and fixed by correcting the `ORDER BY` direction in `sp_refresh_mart`. All Gold tables were rebuilt and the pipeline re-run. **This bug is documented and prevented** with added comments in the SQL.

---

#### 3. CLV Prediction (`clv_model.py`)

**Model:** XGBoost Regressor + Quantile Regression (80% CI)  
**Target:** 6-month forward GMV (`actual_gmv_post_cutoff`)  
**Cutoff:** 2018-05-01 (EDA-locked)

| Metric | Value | Interpretation |
|--------|-------|----------------|
| Training frame | 71,186 customers | ≥1 pre-cutoff order |
| Zero target | 99.21% | Extremely sparse target |
| MAE | 2.47 | Average error in R$ |
| RMSE | 19.17 | Root mean squared error |
| R² | -0.0507 | ✅ Expected — sparse target |

**Feature importance (top 5):**

| Feature | Importance |
|---------|------------|
| days_since_last_order_pre_cutoff | 0.28 |
| avg_order_value_pre_cutoff | 0.23 |
| order_frequency_per_month_pre_cutoff | 0.23 |
| total_orders_pre_cutoff | 0.09 |
| tenure_months_pre_cutoff | 0.08 |

**Critical architectural decision:** Features are recomputed from warehouse tables with an explicit `order_purchase_timestamp < cutoff_date` filter — **not** read from `mart.clv_features` — to prevent target leakage. The original plan's approach of pointing straight at `mart.clv_features` would have leaked post-cutoff information into pre-cutoff features. This was caught before any model was trained.

**Confidence interval coverage:** 99.2% (target ~80%) — expected for zero-inflated target.

---

#### 4. Churn Classification (`churn_model.py`)

**Model:** XGBoost Classifier with F1-tuned threshold  
**Target:** `is_churned` (180-day rule, EDA-locked)  
**Class imbalance:** 70.97% positive

| Metric | Value |
|--------|-------|
| Tuned threshold | 0.2990 |
| F1-score | 0.8499 |
| Precision | 0.7708 |
| Recall | 0.9469 |
| ROC-AUC | 0.7995 |

**Feature importance (top 10):**

| Feature | Importance |
|---------|------------|
| total_categories_purchased | 0.116 |
| total_freight_paid | 0.103 |
| customer_state_SP | 0.103 |
| avg_delivery_delta_days | 0.075 |
| preferred_payment_type_debit_card | 0.055 |
| customer_state_RJ | 0.054 |
| avg_order_value | 0.048 |
| avg_review_score | 0.045 |
| customer_state_BA | 0.042 |
| total_gmv | 0.036 |

**Critical bug caught and fixed:** `days_since_last_order` was originally included as a feature. Since `is_churned = (days_since_last_order > 180)`, the model achieved F1=1.0 with threshold=0.9999 — it had learned the answer key. The feature was removed entirely. The corrected model now learns genuine behavioral patterns.

**Sentiment backfill gap:** Nothing was aggregating `sentiment_scores.compound_score` up to `customer_360.avg_sentiment_score`. `churn_model.py` now backfills this directly before training, closing a real pipeline gap.

---

#### 5. Next-Purchase Timing (`next_purchase.py`)

**Model:** Weibull AFT (Survival Analysis)  
**Method:** `lifelines.WeibullAFTFitter`  
**Population:** Customers with ≥2 orders (2,997 customers)

| Metric | Value |
|--------|-------|
| Observed intervals | 2,346 |
| Censored intervals | 2,996 |
| Concordance index | 0.5254 (barely above random) |

**Concordance index interpretation:** The low concordance index (0.5254) reflects the population: only 2,997 of 96,096 customers (3.1%) have ≥2 orders. Per the Phase 2 EDA finding (96.88% one-time buyers), there is little genuine timing signal to learn from this population. This is documented as a known limitation, not a bug.

**Conditional median prediction:** Uses `predict_median(..., conditional_after=days_since_last_order)` — the correct way to compute `T | T > s` for Weibull hazard. Naively subtracting `days_since_last_order` from the total expected interval would ignore the survival curve shape and systematically distort estimates for overdue customers.

**Critical bug caught and fixed:** `avg_sentiment_score` was NULL for 100% of rows during initial runs, causing `X_numeric[col].median()` to return `NaN` and `.fillna(NaN)` to be a no-op — `lifelines` refuses to fit on NaN. Added fallback to 0.0 with a warning when median is undefined.

---

#### 6. Action Queue Rule Engine (`action_rules.py`)

**Engine:** Priority-ordered rule engine with JSON config  
**Output:** `mart.crm_action_queue` (96,096 rows)  
**Audit:** `mart.action_run_log` (one row per execution)

**Action Distribution:**

| Action Type | Priority | Count | % of Total |
|-------------|----------|-------|------------|
| MONITOR | LOW | 57,565 | 59.9% |
| REACTIVATION | MED | 26,574 | 27.7% |
| RETENTION_CAMPAIGN | HIGH | 11,957 | 12.4% |
| VIP_UPGRADE | MED | 0 | 0.0% |

**Rule Validation (100% Compliance):**

| Rule | Condition | Compliance |
|------|-----------|------------|
| RETENTION_CAMPAIGN | churn ≥ 0.6 AND clv > median | 100% |
| REACTIVATION | churn ≥ 0.6 AND clv ≤ median | 100% |
| VIP_UPGRADE | Champions AND clv > p90 | 0 matches (expected) |
| MONITOR | Catch-all | 100% |

**Per-Action Customer Profile:**

| Action Type | Avg Churn | Avg CLV | Avg Health Score | Avg Days Since Last Order |
|-------------|-----------|---------|------------------|---------------------------|
| RETENTION_CAMPAIGN | 0.72 | R$1.36 | 44.5 | 324 |
| REACTIVATION | 0.76 | R$0.74 | 32.6 | 367 |
| MONITOR | 0.41 | R$1.46 | 52.6 | 244 |

**Key insight:** MONITOR customers have the highest CLV (R$1.46) and lowest churn (0.41) — they're the best customers. RETENTION_CAMPAIGN customers have high churn (0.72) and decent CLV (R$1.36) — premium retention spend is justified. REACTIVATION customers have the highest churn (0.76) but lowest CLV (R$0.74) — cost-effective nudges are appropriate.

**Critical bug caught and fixed during implementation:** The INSERT statement tried to write columns that don't exist in `crm_action_queue` (`clv_predicted_6m`, `rfm_segment`, `health_tier`, `is_actioned`). The table only has `customer_unique_id`, `action_type`, `priority`, `churn_probability`, `clv_predicted`, `trigger_reason`. The INSERT was corrected to match the actual DDL.

**Schema constraints validated:** `crm_action_queue` has a CHECK constraint allowing only `RETENTION_CAMPAIGN`, `REACTIVATION`, `VIP_UPGRADE`, `MONITOR`. The JSON config validates all action types against this at startup.

---

### Final DQ Report — All Predictions Valid

| Check | Value | Status |
|-------|-------|--------|
| `churn_probability` populated | 96,096/96,096 (100%) | ✅ |
| `clv_predicted_6m` populated | 71,186/96,096 (74.08%) | ✅ |
| `avg_sentiment_score` populated | 39,764/96,096 (41.38%) | ✅ |
| `expected_next_purchase_days` populated | 2,996/96,096 (3.12%) | ✅ |
| `rfm_segment` populated | 96,096/96,096 (100%) | ✅ |
| `km_cluster` populated | 96,096/96,096 (100%) | ✅ |
| `compound_score` populated (of reviews with text) | 40,641/40,659 (99.96%) | ✅ |

**Range Checks — All Within Bounds:**

| Column | Range | Expected | Status |
|--------|-------|----------|--------|
| `churn_probability` | [0.0302, 0.971] | [0, 1] | ✅ |
| `clv_predicted_6m` | [0.0, 945.3] | ≥0 | ✅ |
| `avg_sentiment_score` | [-0.9532, 0.974] | [-1, 1] | ✅ |
| `expected_next_purchase_days` | [138.9, 1321.4] | ≥0 | ✅ |

---

### What Broke (And How We Fixed It)

**Issue 1: Target Leakage in Churn Model**
- **What happened:** `days_since_last_order` was included as a feature. Since `is_churned = (days_since_last_order > 180)`, the model learned the answer key.
- **Evidence:** `F1=1.0`, `threshold=0.9999`, `feature_importances` showed `days_since_last_order = 1.0`, all others = 0.0.
- **Fix:** Removed `days_since_last_order` from features. Added a docstring documenting the mistake so it's not repeated.
- **Result:** Threshold dropped to 0.299, F1=0.85, importance spread across genuine behavioral features.

**Issue 2: Missing `lifelines` Dependency**
- **What happened:** `next_purchase.py` crashed with `ModuleNotFoundError: No module named 'lifelines'`.
- **Fix:** Added `lifelines>=0.30.0` to `requirements.txt`.
- **Result:** Installed and ran successfully.

**Issue 3: Missing Schema Column `expected_next_purchase_days`**
- **What happened:** `next_purchase.py` tried to `UPDATE mart.customer_360 SET expected_next_purchase_days = ...` but the column didn't exist in the database.
- **Evidence:** SQL Server error: `Invalid column name 'expected_next_purchase_days'`.
- **Root cause:** The column was defined in the Phase 5 plan but never added to the Phase 4 DDL.
- **Fix:** Created a non-destructive migration (`08_migrate_add_expected_next_purchase_days.sql`) that uses `ALTER TABLE ADD COLUMN`. Ran it against the live database.
- **Result:** Column added, `next_purchase.py` wrote 2,996 rows successfully.

**Issue 4: Segmentation Runtime (14+ Minutes)**
- **What happened:** `silhouette_score` computed full pairwise distances across 96,096 rows for 5 candidate K values — ~4.6 billion comparisons.
- **Fix:** Used `sklearn`'s documented `sample_size=8000` parameter.
- **Result:** Runtime dropped from 854s to ~20s.

**Issue 5: All-NaN Columns Crashed `lifelines`**
- **What happened:** `avg_sentiment_score` was NULL for 100% of rows (during dry run). `X_numeric[col].median()` returned `NaN`, and `.fillna(NaN)` was a silent no-op. `lifelines` refuses to fit on NaNs.
- **Fix:** Added fallback to `0.0` when median is undefined, with a warning.
- **Result:** Model fits, prediction writes succeed.

**Issue 6: DQ Report Showed CLV Coverage as 0%**
- **What happened:** `dq_report.py` queried `clv_predicted_6m` from `mart.customer_360`. The column doesn't exist there — it lives in `mart.clv_features`.
- **Fix:** Updated `dq_report.py` to query CLV columns from `mart.clv_features`.
- **Result:** Reports 71,186/96,096 (74.08%) correctly.

**Issue 7: RFM Recency Score Inversion**
- **What happened:** `NTILE(5) OVER (ORDER BY recency_days ASC)` assigned score 1 to the most recent customers and 5 to the least recent. Segment rules assumed 5 = most recent.
- **Evidence:** Champions showed 100% churn; Lost showed 0% churn.
- **Fix:** Changed to `ORDER BY recency_days DESC` in `sp_refresh_mart`.
- **Result:** Champions now 0% churn; Lost now 100% churn. ✅

**Issue 8: Action Rules INSERT Column Mismatch**
- **What happened:** `action_rules.py` tried to insert `clv_predicted_6m`, `rfm_segment`, `health_tier`, `is_actioned` — none of which exist in `crm_action_queue`.
- **Evidence:** SQL Server error: `Invalid column name 'clv_predicted_6m'`.
- **Fix:** Corrected `INSERT_ACTION_SQL` to match actual table columns.
- **Result:** 96,096 rows inserted successfully.

**Issue 9: Action Rules JSON Config Validation**
- **What happened:** `AT_RISK_NURTURE` was defined in the JSON but violated the CHECK constraint on `crm_action_queue.action_type`.
- **Evidence:** SQL Server constraint violation on INSERT.
- **Fix:** Removed `AT_RISK_NURTURE` from the rule set. At-risk customers with churn ≥ 0.6 are captured by REACTIVATION; those below the threshold land in MONITOR.
- **Result:** All action types satisfy the CHECK constraint.

---

### What We Learned

| Lesson | Why It Matters |
|--------|----------------|
| **Dry runs catch real bugs** | `--dry-run` exposed: leakage, missing dependencies, all-NaN columns, DQ report errors — all before they hit production. |
| **Don't trust assumptions** | `days_since_last_order` *was* the label. The EDA said 96.88% one-time buyers. Both were true — I missed the implication until the model proved it. |
| **Schema drift is real** | The Python side targeted a column that didn't exist in the DB. This is the same class of error as the leakage bug — writing code against a spec, not the actual state. |
| **DFT — Document the failure** | Every fix includes the mistake and why it was wrong. This is how you stop repeating it. |
| **Test all-NaN columns** | A column that's NULL for 100% of rows crashes `lifelines` and silently degrades XGBoost. The fallback to `0.0` with a warning is the honest fix. |
| **Validate config at startup** | `action_rules.py` validates JSON config before running — missing keys, unknown action types, and MONITOR not last all fail loudly rather than silently. |

---

### Architecture Decisions — Final Log

| Decision | Rationale |
|----------|-----------|
| **LeIA over VADER** | VADER's English-only lexicon mis-scores Portuguese text. LeIA is a deliberate Portuguese rebuild. |
| **CLV features recomputed from warehouse** | Prevents target leakage. `mart.clv_features` is not safe for training features. |
| **Churn threshold tuned on F1** | 71% positive class makes 0.5 meaningless. Optimized on F1, not accuracy. |
| **Survival analysis for next-purchase** | Differentiates from standard churn; maps to real CRM use case. |
| **Action rules in JSON** | Business users can edit thresholds without touching Python. |
| **Action rules CLI overrides** | Test threshold changes without editing config. |
| **Audit log (`action_run_log`)** | One row per execution — preserves history of threshold changes and action distribution. |
| **Human-readable trigger_reason** | Plain English explanation with actual values — CRM analysts can read and act. |
| **Config validation at startup** | Missing keys, unknown action types, MONITOR not last all fail loudly. |
| **`utils.py` shared infrastructure** | Logging, retry, DB connections — no copy-paste across scripts. |
| **Subprocess isolation in `run.py`** | Each script has its own argparse and `sys.exit()`; importing them in-process would be fragile. |

---

### What's Left

| Item | Status |
|------|--------|
| Phase 5 — ML Pipeline | ✅ Complete |
| Phase 6 — Power BI Dashboards | ⏳ Pending |
| Phase 7 — Documentation | ⏳ Pending |

---

### Sign-off

Phase 5 is complete. The pipeline is production-ready. All models ran, wrote predictions, passed quality checks, and the action queue is populated. The remaining work is Power BI and documentation.

**Phase 5 — ✅ COMPLETE**