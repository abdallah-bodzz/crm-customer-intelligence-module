## Phase 6 Completion Report — Power BI Dashboards

**Status:** ✅ COMPLETE  
**Date:** 2026-06-30   
**Lead-dev**Abdallah A Khames      
**github** `abdallah-bodzz`       
**repo** `crm-customer-intelligence-module`       
**Artifacts:** `.pbix` file, PDF export, 10 screenshots, video recording, 2 theme files, DAX script, implementation guide, blueprint HTML

---

### Executive Summary

Phase 6 is fully implemented. A 7‑page Power BI report was built from the medallion lakehouse, providing business users with actionable CRM dashboards. The report includes 29 DAX measures, 7 calculated columns, 14 bookmarks, and a consistent left‑rail navigation system. All pages validated against the source data.

**Key deliverable:** The implementation guide (`PowerBI_Dashboard_Implementation_Guide.md`) documents every visual position, field drop, DAX formula, and bookmark — a developer can rebuild the entire report from scratch without guesswork.

---

### Dashboard Overview

| Page | User | Purpose |
|------|------|---------|
| **Command Centre** | CEO / Head of Retention | Portfolio‑level snapshot — KPIs, action distribution, GMV trend, health tier, top states |
| **Customer 360** | Customer Success | Single‑customer account view — identity card, order timeline, delivery profile, sentiment strip, trigger reason |
| **Churn & Action Risk** | Retention Manager | Daily operational triage — ranked customer table, churn driver breakdown, histogram, segment churn rates |
| **Segmentation & RFM** | CRM Analyst | Segment strategy — RFM scatter, segment‑action heatmap, segment distribution, K‑means clusters |
| **CLV & Predicted Value** | Revenue Forecasting | Customer value prioritisation — CLV histogram, CI band, quadrant scatter, feature importance |
| **Geo Intelligence** | Territory Manager | Regional operations — Brazil filled map, state ranking, delivery vs satisfaction scatter |
| **Sentiment & NLP** | CX Analyst | Review monitoring — compound score distribution, sentiment by star rating, monthly trend |

**Tooltip page:** RFM Tooltip (scatter hover)  
**Hidden page:** Customer 360 (drill‑through only)

---

### Data Model

**Imported tables (7):**
- `CustomerHealth` (`mart.vw_customer_health`) — primary flat view
- `ChurnSignals` (`mart.vw_churn_signals`) — at‑risk customers
- `GeoPerformance` (`mart.vw_geo_performance`) — state aggregates
- `SentimentScores` (`mart.sentiment_scores`) — review sentiment
- `ActionQueue` (`mart.crm_action_queue`) — action history
- `ActionRunLog` (`mart.action_run_log`) — audit log
- `RefreshLog` (`mart.refresh_log`) — shared clock

**Additional queries (8):**
- `MonthlyGMV`, `RFMAggregated`, `CLVBands`, `CLVResiduals`, `CLVQuadrant`, `OrderTimeline`, `SentimentHistogram`, `SentimentTrend`

**Relationships:**
- `CustomerHealth` (1:Many) → `SentimentScores`, `ActionQueue`, `OrderTimeline`
- `ChurnSignals`, `GeoPerformance`, and all query tables are standalone visual sources

---

### DAX & Calculated Columns

**Measures (29):**
- Portfolio KPIs (7): `Total GMV`, `Total Customers`, `Avg Order Value`, `Total Freight Paid`, `Freight Ratio`, `As Of Date`, `As Of Date Label`
- Churn (9): `Churned Customers`, `Churn Rate`, `Avg Churn Probability`, `High Churn Customers`, `High Churn Rate`, `Avg Urgency Score`, `Avg Pct Late`, `Avg Delivery Delta`, `Avg Review Score`, `Avg Health Score`, `Segment Churn Rate`
- Action Queue (9): `HIGH/MED/LOW Priority Count`, `Actionable Customers`, `Actionable Pct`, `Flagged GMV`, `Retention GMV`, `Reactivation GMV`, `Retention/Reactivation/Monitor Count`
- CLV (8): `CLV With Prediction`, `CLV Coverage Pct`, `Avg CLV Predicted`, `Median CLV`, `Max CLV`, `P90 CLV`, `Total CLV Portfolio`, `Avg GMV per Customer`
- Sentiment (7): `Reviews Scored`, `Reviews With Text`, `Pct Positive/Neutral/Negative Sentiment`, `Avg Sentiment`, `Avg Compound Score`, `Sarcasm Flag Count`
- Geo (5): `National GMV`, `State GMV Share`, `Avg State Churn Rate`, `Avg State Late Pct`, `Avg State Review Score`
- What‑If (5): `WhatIf Actionable`, `WhatIf Retention Campaign`, `WhatIf Reactivation`, `WhatIf Delta Actionable`, `WhatIf Delta Label`
- Next‑Purchase (3): `Next Purchase With Prediction`, `Avg Next Purchase Days`, `Min Next Purchase Days`
- Diagnostic (6): `_Data Validation`, `_Row Count CustomerHealth`, `_Row Count SentimentScores`, `_Row Count ActionQueue`, `_Null CLV Count`, `_Null Sentiment Count`

**Calculated Columns (7):**
- `CLV Band` & `CLV Band Sort` — P5 histogram buckets
- `Churn Prob Bucket` & `Churn Prob Bucket Sort` — P3 histogram
- `Is Churn Risk` — 0.6 threshold flag
- `CLV Churn Quadrant` — P5 quadrant scatter
- `Priority Sort` — action priority ordering

**What‑If Parameter:** `Threshold Parameter` (0.2–0.9, increment 0.05, default 0.6)

---

### Issues Encountered & Resolved

**Issue: CLV Columns Blank in Power BI**

**Symptom:** All CLV & Predicted Value page cards and charts showed blank values, despite `clv_model.py` having written 71,186 predictions to `mart.clv_features`.

**Diagnosis:**
- `mart.vw_customer_health` included the column `clv_predicted_6m` (verified via `INFORMATION_SCHEMA.COLUMNS`).
- Querying the view with `WHERE clv_predicted_6m IS NOT NULL` returned 0 rows.
- Root cause: The view selected `c360.clv_predicted_6m` (`mart.customer_360`'s dead copy, which nothing ever writes to) instead of `clv.clv_predicted_6m` (`mart.clv_features`' populated copy).

**Evidence:**
- `mart.customer_360` has a `clv_predicted_6m` column defined in DDL but never populated (Python writes only to `clv_features`).
- `01_vw_customer_health.sql` line 1 of the SELECT list: `c360.clv_predicted_6m` — wrong table alias.

**Fix:**
1. Updated `sql/04_views/01_vw_customer_health.sql`:
   ```sql
   -- Before
   c360.clv_predicted_6m,
   -- After
   clv.clv_predicted_6m,
   ```
2. Re‑executed `01_vw_customer_health.sql` (`CREATE OR ALTER VIEW` — idempotent).
3. Verified:
   ```sql
   SELECT COUNT(*) FROM mart.vw_customer_health WHERE clv_predicted_6m IS NOT NULL;
   -- returned 71,186
   ```
4. Refreshed Power BI dataset — all CLV visuals populated correctly.

**Blast radius:** `action_rules.py` and `dq_report.py` read `clv_predicted_6m` directly from `mart.clv_features`, never through the view. The action queue and DQ report were unaffected. Only Power BI (and `vw_churn_signals`, which inherits from `vw_customer_health`) was affected.

**Prevention:** Added a regression check to `07_verify_mart.sql`:
```sql
SELECT
    (SELECT COUNT(*) FROM mart.vw_customer_health WHERE clv_predicted_6m IS NOT NULL) AS view_clv_non_null,
    (SELECT COUNT(*) FROM mart.clv_features WHERE clv_predicted_6m IS NOT NULL) AS table_clv_non_null;
-- expect equal
```

**Follow‑up (optional):** Drop the dead column `mart.customer_360.clv_predicted_6m` to prevent future alias confusion. Migration script: `sql/03_mart/10_migrate_drop_dead_clv_column.sql`.

---

### Bookmark Inventory (14)

| Bookmark | Page | Effect |
|----------|------|--------|
| `P1_Top5` | P1 | Default — top 5 state bar |
| `P1_Pareto` | P1 | All states + cumulative Pareto line |
| `P3_Operational` | P3 | Normal churn view (default) |
| `P3_WhatIf` | P3 | What‑If simulator panel visible |
| `P4_RFM` | P4 | RFM rule‑based view (default) |
| `P4_KMeans` | P4 | K‑means cluster view |
| `P5_Default` | P5 | Normal CLV view (default) |
| `P5_ActualVsPredicted` | P5 | Residual scatter visible |
| `P6_GMV` | P6 | Map = GMV (default) |
| `P6_Churn` | P6 | Map = Churn rate |
| `P6_Late` | P6 | Map = % late deliveries |
| `P6_Review` | P6 | Map = Avg review score |
| `P7_Default` | P7 | Normal sentiment view (default) |
| `P7_Sarcasm` | P7 | Sarcasm table visible |

**Note:** All bookmarks have "Data" unchecked (preserve slicer state) except the P6 map toggles.

---

### Theme & Styling

| Theme | File | Use Case |
|-------|------|----------|
| **Warm Clay** (light) | `light-theme.json` | PDF export, printing, daylight viewing |
| **Ember** (dark) | `dark-theme.json` | Presentations, dark mode environments |

**Colour encoding — consistent across all pages:**
- Health tiers: High=teal, Medium=amber, Low=coral
- Action types: RETENTION=coral, REACTIVATION=amber, MONITOR=gray
- Segments: 9‑colour fixed palette (Champions=teal, Lost=gray, etc.)

---

### Deliverables

```
powerbi/
├── CRM_Customer_Intelligence - Dark_theme.pbix
├── CRM_Customer_Intelligence - Dark_theme.pdf
├── CRM_Customer_Intelligence.pbix
├── CRM_Customer_Intelligence.pdf
├── CRM_Intelligence_Measures.dax.md          # All DAX measures + calculated columns
├── dark-theme.json                           # Dark theme (Ember)
├── light-theme.json                          # Light theme (Warm Clay)
├── powerbi_blueprint.html                    # Visual specification
├── PowerBI_Dashboard_Implementation_Guide.md # Full build guide (8 parts)
├── screenshots/
│   ├── Churn & Action Risk screenshot.png
│   ├── CLV & Predicted Value screenshot.png
│   ├── Command Centre screenshot.png
│   ├── Customer 360 screenshot.png
│   ├── Geo Intelligence screenshot.png
│   ├── model-view-screenshot.png
│   ├── RFM Tooltip page screenshot.png
│   ├── Segmentation & RFM screenshot.png
│   ├── Segmentation and RFM screenshot.png
│   └── Sentiment & NLP screenshot.png
└── vid-recored/
    ├── vid-recored.mp4
    └── vid-recored.mp4.gif
```

---

### Validation Checklist

| Check | Result |
|-------|--------|
| DAX EVALUATE block returns `"PASS — all checks cleared"` | ✅ |
| `Total GMV` = R$15.84M | ✅ |
| `Total Customers` = 96,096 | ✅ |
| `HIGH Priority Count` = 11,957 | ✅ |
| `Reviews Scored` = 40,641 | ✅ |
| `_Row Count ActionQueue` = 96,096 | ✅ |
| `_Null CLV Count` ≈ 24,910 | ✅ |
| All 7 pages render without errors | ✅ |
| All 14 bookmarks work | ✅ |
| Drill‑through to Customer 360 works from all pages | ✅ |
| Navigation rail buttons work from all pages | ✅ |
| CLV columns populated (after fix) | ✅ |
| Map renders with Brazilian states | ✅ |

---

### Architecture Decisions — Final Log

| Decision | Rationale |
|----------|-----------|
| **Import mode** | Views contain complex joins and window functions; DirectQuery performance would be unusable |
| **One flat view (`vw_customer_health`)** | Replaces importing 3 separate mart tables and building relationships in Power BI |
| **Left navigation rail with bookmarks** | Hides default page tabs; active‑state indicator tracks current page |
| **Aggregated RFM scatter (125 rows)** | 96k individual dots freeze Power BI; pre‑grouped by R/F/M score combinations |
| **`_Measures` table** | All DAX measures in one dedicated table — organised, discoverable |
| **`_` prefix on diagnostic measures** | Sorts diagnostic measures to the bottom of the field list |

---

### Sign‑off

Phase 6 is complete. The Power BI report is production‑ready, validated against the Gold layer, and matches the blueprint. The CLV column bug was identified, fixed, and documented with a regression check to prevent recurrence.