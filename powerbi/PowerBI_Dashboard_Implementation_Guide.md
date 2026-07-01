# Power BI Dashboard Implementation Guide
## CRM Customer Intelligence Module — Olist E-Commerce

**Version:** 2.1  
**Database:** `CRM_Analytics` (SQL Server)  
**Data connection:** Import mode from SQL Server  
**Theme files:** `CRM_Intelligence_Dark.json` (Obsidian) · `CRM_Intelligence_Light.json` (Chalk)  
**DAX script:** `CRM_Intelligence_Measures.dax`  
**Pages:** 7 (P1 Command Centre · P2 Customer 360 · P3 Churn & Action Risk · P4 Segmentation & RFM · P5 CLV & Predicted Value · P6 Geo Intelligence · P7 Sentiment & NLP)  
**Tooltip pages:** 1 (RFM Tooltip)  
**Hidden pages:** 1 (P2 Customer 360 — drill-through only)  

**Changes from v2.0:**
- Part 3.3 (left navigation rail) rebuilt as an explicit two-pass process — skeleton now, bookmark wiring after Part 5 — instead of one pass that referenced bookmarks before they exist
- Nav rail button-to-page-to-bookmark mapping made explicit in a single reference table
- Active-state indicator logic clarified: one indicator shape per page, visibility set per bookmark via Update, not via the shape itself
- Icon-font fallback caveat added for the rail's glyph labels
- Data-as-of strip and narrative card split out of "the nav rail" into their own numbered steps (3.4, 3.5) since they're unrelated, full-width elements
- Colour encoding renumbered 3.4 → 3.6; internal cross-references updated

**Changes from v1.0:**
- Theme files added — apply before building any visual
- DAX measures replaced with `CRM_Intelligence_Measures.dax` — run in Query View
- Calculated columns: Section 10 of the DAX file — create manually in Model view
- P2 source list corrected — `vw_customer_health` added
- P4 RFM scatter: aggregated query mandated (performance fix documented)
- P5: "Actual vs Predicted" bookmark documented fully
- P6: All 4 map metric bookmarks made explicit
- P3: What-If simulator fully documented with Threshold Parameter setup
- Sarcasm table (P7): filter logic corrected
- Anomaly outliers table (P1) added
- Narrative cards added per page
- Build order confirmed: P1 → P6 → P3 → P4 → P5 → P7 → P2

---

## PART 0 — BEFORE YOU OPEN POWER BI DESKTOP

### 0.1 — Confirm all pipeline steps have run

Run in SSMS before connecting Power BI. Every number must match.

```sql
SELECT 'customer_360'      AS tbl, COUNT(*) AS row_count FROM mart.customer_360
UNION ALL SELECT 'rfm_features',     COUNT(*) FROM mart.rfm_features
UNION ALL SELECT 'clv_features',     COUNT(*) FROM mart.clv_features
UNION ALL SELECT 'sentiment_scores', COUNT(*) FROM mart.sentiment_scores
UNION ALL SELECT 'crm_action_queue', COUNT(*) FROM mart.crm_action_queue
UNION ALL SELECT 'action_run_log',   COUNT(*) FROM mart.action_run_log;
-- Expected: 96096 / 96096 / 96096 / ~99224 / 96096 / ≥1

-- Confirm ML columns populated
SELECT
    SUM(CASE WHEN churn_probability IS NULL THEN 1 ELSE 0 END)   AS null_churn,
    SUM(CASE WHEN clv_predicted_6m  IS NULL THEN 1 ELSE 0 END)   AS null_clv,
    SUM(CASE WHEN avg_sentiment_score IS NULL THEN 1 ELSE 0 END) AS null_sentiment
FROM mart.customer_360;
-- Expected: 0 / ~24910 / ~56332
-- If null_churn > 0: run churn_model.py before proceeding
```

### 0.2 — Tables and views to import

Import exactly these 7 objects. Do not import Silver (`warehouse.*`) or Bronze (`staging.*`) tables — the views already contain everything you need.

| Object | Type | Rename to in Power BI |
|--------|------|-----------------------|
| `mart.vw_customer_health` | View | `CustomerHealth` |
| `mart.vw_churn_signals` | View | `ChurnSignals` |
| `mart.vw_geo_performance` | View | `GeoPerformance` |
| `mart.sentiment_scores` | Table | `SentimentScores` |
| `mart.crm_action_queue` | Table | `ActionQueue` |
| `mart.action_run_log` | Table | `ActionRunLog` |
| `mart.refresh_log` | Table | `RefreshLog` |

`vw_customer_health` joins `customer_360 + rfm_features + clv_features + crm_action_queue` (latest action per customer) in one flat view. You do not need the mart tables individually — importing them separately and joining in Power BI recreates work the SQL view already does, adds model complexity, and risks relationship errors.

**Additional queries to import** (added below in Part 0.5 — these are not SQL views, they are ad-hoc queries that Power BI cannot derive from the main tables alone):
- `MonthlyGMV` — monthly GMV trend for P1-V3
- `RFMAggregated` — aggregated RFM scatter for P4-V1 (performance fix)
- `CLVBands` — sampled CLV confidence interval data for P5-V4
- `CLVResiduals` — actual vs predicted scatter for P5 bookmark
- `CLVQuadrant` — quadrant aggregates for P5-V5
- `OrderTimeline` — per-order timeline for P2-V2
- `SentimentHistogram` — bucketed compound scores for P7-V2
- `SentimentTrend` — monthly sentiment trend for P7-V4

### 0.3 — Import procedure

1. Power BI Desktop → Home → Get data → SQL Server
2. Server: your SQL Server instance name. Database: `CRM_Analytics`.
3. Connectivity mode: **Import**. Do not use DirectQuery — the views contain multi-table joins and window functions; DirectQuery performance will be unusable.
4. Navigator: select all 7 objects. Click **Transform Data**.
5. Power Query: for each query, verify the row count in the status bar matches Part 0.1 expectations. Rename each table as shown in 0.2. Click **Close & Apply**.
6. For the additional queries in 0.5: Home → New Source → SQL Server → paste each query into the SQL Statement box → name it → Load.

### 0.4 — Relationships to create

Model view → Manage relationships → New:

| From table | From column | To table | To column | Cardinality | Active |
|-----------|-------------|----------|-----------|-------------|--------|
| `CustomerHealth` | `customer_unique_id` | `SentimentScores` | `customer_unique_id` | 1:Many | Yes |
| `CustomerHealth` | `customer_unique_id` | `ActionQueue` | `customer_unique_id` | 1:Many | Yes |
| `CustomerHealth` | `customer_unique_id` | `OrderTimeline` | `customer_unique_id` | 1:Many | Yes |

`GeoPerformance`, `ChurnSignals`, `RFMAggregated`, `CLVBands`, `CLVResiduals`, `CLVQuadrant`, `MonthlyGMV`, `SentimentHistogram`, `SentimentTrend`, `RefreshLog`, `ActionRunLog` — no relationships. Used as standalone visual sources on their respective pages.

Do not create a relationship between `CustomerHealth` and `ChurnSignals`. `ChurnSignals` is a filtered subset of `CustomerHealth` — joining them creates circular ambiguity. Use each as its own visual source on its page.

### 0.5 — Additional SQL queries to import as Power BI tables

Import each of these via: Home → New Source → SQL Server → paste query into SQL Statement box → name and load.

**MonthlyGMV** — P1 area chart (monthly GMV trend):
```sql
SELECT
    CAST(DATEADD(MONTH, DATEDIFF(MONTH, 0, fo.order_purchase_timestamp), 0) AS DATE) AS order_month,
    SUM(foi.gmv) AS monthly_gmv,
    COUNT(DISTINCT fo.order_id) AS order_count
FROM warehouse.fact_orders fo
JOIN warehouse.fact_order_items foi ON fo.order_id = foi.order_id
WHERE fo.order_purchase_timestamp IS NOT NULL
  AND fo.order_purchase_date_sk <> 19000101
GROUP BY DATEADD(MONTH, DATEDIFF(MONTH, 0, fo.order_purchase_timestamp), 0)
ORDER BY 1;
```

**RFMAggregated** — P4 RFM scatter (96k dots → 125 rows, performance fix):
```sql
SELECT
    r.recency_score,
    r.monetary_score,
    r.frequency_score,
    r.rfm_segment,
    COUNT(*)                            AS customer_count,
    AVG(CAST(r.monetary AS FLOAT))      AS avg_gmv,
    AVG(CAST(r.recency_days AS FLOAT))  AS avg_recency_days,
    AVG(CAST(c.churn_probability AS FLOAT)) AS avg_churn_prob,
    AVG(CAST(c.customer_health_score AS FLOAT)) AS avg_health_score
FROM mart.rfm_features r
JOIN mart.customer_360 c ON r.customer_unique_id = c.customer_unique_id
GROUP BY r.recency_score, r.monetary_score, r.frequency_score, r.rfm_segment;
```

**CLVBands** — P5 CI band chart:
```sql
SELECT TOP 5000
    cf.customer_unique_id,
    cf.clv_predicted_6m,
    cf.clv_ci_lower,
    cf.clv_ci_upper,
    NTILE(100) OVER (ORDER BY cf.clv_predicted_6m) AS clv_percentile_rank
FROM mart.clv_features cf
WHERE cf.clv_predicted_6m IS NOT NULL
ORDER BY cf.clv_predicted_6m;
```

**CLVResiduals** — P5 Actual vs Predicted bookmark:
```sql
SELECT
    cf.customer_unique_id,
    cf.actual_gmv_post_cutoff,
    cf.clv_predicted_6m,
    cf.clv_predicted_6m - cf.actual_gmv_post_cutoff AS residual,
    c.rfm_segment,
    c.customer_state
FROM mart.clv_features cf
JOIN mart.customer_360 c ON cf.customer_unique_id = c.customer_unique_id
WHERE cf.clv_predicted_6m IS NOT NULL
  AND cf.actual_gmv_post_cutoff IS NOT NULL;
```

**CLVQuadrant** — P5 quadrant scatter (aggregated, performance fix):
```sql
SELECT
    CASE
        WHEN c.churn_probability >= 0.6 AND cf.clv_predicted_6m >= 0.93 THEN 'Retain'
        WHEN c.churn_probability <  0.6 AND cf.clv_predicted_6m >= 0.93 THEN 'Grow'
        WHEN c.churn_probability >= 0.6 AND (cf.clv_predicted_6m < 0.93
             OR cf.clv_predicted_6m IS NULL)                            THEN 'Reactivate'
        ELSE 'Monitor'
    END                                 AS quadrant,
    COUNT(*)                            AS customer_count,
    AVG(c.churn_probability)            AS avg_churn,
    AVG(cf.clv_predicted_6m)            AS avg_clv,
    SUM(CAST(c.total_gmv AS FLOAT))     AS total_gmv
FROM mart.clv_features cf
JOIN mart.customer_360 c ON cf.customer_unique_id = c.customer_unique_id
WHERE cf.clv_predicted_6m IS NOT NULL
GROUP BY
    CASE
        WHEN c.churn_probability >= 0.6 AND cf.clv_predicted_6m >= 0.93 THEN 'Retain'
        WHEN c.churn_probability <  0.6 AND cf.clv_predicted_6m >= 0.93 THEN 'Grow'
        WHEN c.churn_probability >= 0.6 AND (cf.clv_predicted_6m < 0.93
             OR cf.clv_predicted_6m IS NULL)                            THEN 'Reactivate'
        ELSE 'Monitor'
    END;
```

**OrderTimeline** — P2 order timeline:
```sql
SELECT
    fo.customer_unique_id,
    fo.order_id,
    CAST(fo.order_purchase_timestamp AS DATE)           AS order_date,
    foi.total_order_gmv,
    fo.delivery_delta_days,
    dr.review_score,
    ss.compound_score,
    ss.sentiment_label,
    DATEDIFF(DAY, '2016-09-01', fo.order_purchase_timestamp) AS days_from_start
FROM warehouse.fact_orders fo
JOIN (
    SELECT order_id, SUM(gmv) AS total_order_gmv
    FROM warehouse.fact_order_items GROUP BY order_id
) foi ON fo.order_id = foi.order_id
LEFT JOIN warehouse.dim_review dr ON fo.order_id = dr.order_id
LEFT JOIN mart.sentiment_scores ss ON dr.review_id = ss.review_id
WHERE fo.order_purchase_timestamp IS NOT NULL
  AND fo.order_purchase_date_sk <> 19000101;
```

**SentimentHistogram** — P7 compound score distribution:
```sql
SELECT
    CASE
        WHEN compound_score < -0.6  THEN 1
        WHEN compound_score < -0.3  THEN 2
        WHEN compound_score < -0.05 THEN 3
        WHEN compound_score <= 0.05 THEN 4
        WHEN compound_score <= 0.3  THEN 5
        WHEN compound_score <= 0.6  THEN 6
        ELSE 7
    END                             AS bucket_order,
    CASE
        WHEN compound_score < -0.6  THEN '-1.0 to -0.6'
        WHEN compound_score < -0.3  THEN '-0.6 to -0.3'
        WHEN compound_score < -0.05 THEN '-0.3 to -0.05'
        WHEN compound_score <= 0.05 THEN '-0.05 to 0.05'
        WHEN compound_score <= 0.3  THEN '0.05 to 0.3'
        WHEN compound_score <= 0.6  THEN '0.3 to 0.6'
        ELSE '0.6 to 1.0'
    END                             AS compound_bucket,
    COUNT(*)                        AS review_count
FROM mart.sentiment_scores
WHERE compound_score IS NOT NULL
GROUP BY
    CASE WHEN compound_score < -0.6 THEN 1 WHEN compound_score < -0.3 THEN 2
         WHEN compound_score < -0.05 THEN 3 WHEN compound_score <= 0.05 THEN 4
         WHEN compound_score <= 0.3 THEN 5 WHEN compound_score <= 0.6 THEN 6
         ELSE 7 END,
    CASE WHEN compound_score < -0.6 THEN '-1.0 to -0.6' WHEN compound_score < -0.3 THEN '-0.6 to -0.3'
         WHEN compound_score < -0.05 THEN '-0.3 to -0.05' WHEN compound_score <= 0.05 THEN '-0.05 to 0.05'
         WHEN compound_score <= 0.3 THEN '0.05 to 0.3' WHEN compound_score <= 0.6 THEN '0.3 to 0.6'
         ELSE '0.6 to 1.0' END
ORDER BY 1;
```

**SentimentTrend** — P7 monthly sentiment trend:
```sql
SELECT
    CAST(DATEADD(MONTH, DATEDIFF(MONTH, 0, ss.review_creation_date), 0) AS DATE) AS review_month,
    AVG(ss.compound_score)  AS avg_compound,
    COUNT(*)                AS review_count
FROM mart.sentiment_scores ss
WHERE ss.compound_score IS NOT NULL
  AND ss.review_creation_date IS NOT NULL
GROUP BY DATEADD(MONTH, DATEDIFF(MONTH, 0, ss.review_creation_date), 0)
ORDER BY 1;
```

---

## PART 1 — THEME APPLICATION

**Files:**
- `CRM_Intelligence_Warm_Clay.json` (light)
- `CRM_Intelligence_Ember.json` (dark)

Apply the theme before building any visual. If you apply it after, all manually set colours on existing visuals will be overwritten.

---

### Steps

1. **View** tab → **Themes** → **Browse for themes**
2. Navigate to your project folder → `powerbi/` → select the theme file
3. Click **Open**. Power BI applies the theme immediately.
4. **Confirm:** page background should change to `#F5F0EA` (light) or `#1A1715` (dark). Visual backgrounds update accordingly.

---

### Which Theme to Use

| Theme | Use Case |
|-------|----------|
| **Warm Clay (light)** | PDF export, printing, stakeholders reading in daylight, sharing as static report. |
| **Ember (dark)** | Presenting on screens/projectors, dark mode environments. Data colours pop more on dark background. |

---

### Theme Colour Tokens

| Token | Light Hex | Dark Hex | Used For |
|-------|-----------|----------|----------|
| Teal | `#5FA968` | `#7BAF6B` | Good, Champions, High health, Positive sentiment |
| Amber | `#D4936A` | `#E0935A` | Medium health, Reactivation |
| Coral | `#B45F3A` | `#D9714A` | Bad, Retention Campaign, Low health, Negative sentiment |
| Purple | `#7A5F9E` | `#A892C9` | Loyal segment |
| Blue | `#3E7E9E` | `#6FA8C9` | Potential Loyalist, CLV, Geo |
| Green | `#6B8B5A` | `#7A9B8F` | CLV high tier |
| Gray | `#8F6F5A` | `#A68B7A` | Monitor, Neutral |

---

### What the Theme Sets Automatically

| Element | Light | Dark |
|---------|-------|------|
| Page background | `#F5F0EA` | `#1A1715` |
| Visual background | `#FCF9F6` | `#24201C` |
| Visual border | `#857565` | `#5A5246` |
| Text | `#3D3630` | `#E8E0D8` |
| Secondary text | `#7A7068` | `#988C82` |
| Gridlines | `#EDE5DC` | `#2C2824` |
| Filter pane background | `#F5F0EA` / `#FCF9F6` | `#1A1715` / `#24201C` |
| Card background | `#FCF9F6` | `#24201C` |
| Table header | `#B45F3A` (coral) | `#D9714A` (terracotta) |
| Visual corners | `30px` rounded | `30px` rounded |
| Font | Inter (light) | DM Sans (dark) |

---

### Manual Overrides (Apply Per Visual)

These cannot be set in the theme JSON — apply manually on each relevant visual:

| Visual Type | Apply These Colours |
|-------------|---------------------|
| Action type donut/table | RETENTION = coral, REACTIVATION = amber, MONITOR = gray |
| Health tier visuals | High = teal, Medium = amber, Low = coral |
| RFM segment charts | Segment palette (9 colours — see Part 3.6) |

---

### How to Verify Theme Applied Correctly

1. Page background is **not** pure white (light) or pure black (dark)
2. Visuals have **rounded corners** (`30px`)
3. Filter pane matches the theme background
4. Text is readable and uses **Inter** (light) or **DM Sans** (dark) font

If any of these are missing, re‑apply the theme.

---

## PART 2 — DAX MEASURES AND CALCULATED COLUMNS

### 2.1 — Run the DAX script

The file `CRM_Intelligence_Measures.dax` contains all measures and calculated column definitions. Execute it as follows:

1. In Power BI Desktop, first create the `_Measures` placeholder table:
   - Home → Enter Data
   - Add one column named `_` with value `(blank)`
   - Name the table `_Measures`
   - Click Load

2. Open DAX Query View: View → DAX Query View (or click the formula icon in the left rail)

3. Paste the entire contents of `CRM_Intelligence_Measures.dax` into the query editor

4. Click **Run** (or Shift+Enter)

5. When prompted: click **"Update model with changes"** — this commits all measures to the `_Measures` table

6. Check the EVALUATE result row at the bottom of the query results pane:
   - `Status` column must show `"PASS — all checks cleared"`
   - `Total GMV (R$)` must show approximately `15843553`
   - `Total Customers` must show `96096`
   - `HIGH Priority Actions` must show `11957`
   - If any show FAIL: re-run the relevant Python pipeline script before continuing

### 2.2 — Create calculated columns (manual — cannot be done in Query View)

DAX Query View cannot create calculated columns. For each column below, go to Model view → select the `CustomerHealth` table → click **New column** in the ribbon → paste the formula.

**Column 1: CLV Band** (used in P5 histogram X-axis)
```dax
CLV Band =
    SWITCH(
        TRUE(),
        ISBLANK(CustomerHealth[clv_predicted_6m]), "No CLV",
        CustomerHealth[clv_predicted_6m] = 0,      "R$0",
        CustomerHealth[clv_predicted_6m] <= 1,     "R$0.01–1",
        CustomerHealth[clv_predicted_6m] <= 5,     "R$1–5",
        CustomerHealth[clv_predicted_6m] <= 20,    "R$5–20",
        CustomerHealth[clv_predicted_6m] <= 100,   "R$20–100",
        "R$100+"
    )
```

**Column 2: CLV Band Sort** (sort column for CLV Band — do not use directly in visuals)
```dax
CLV Band Sort =
    SWITCH(
        CustomerHealth[CLV Band],
        "No CLV",   0,  "R$0",      1,  "R$0.01–1", 2,
        "R$1–5",    3,  "R$5–20",   4,  "R$20–100", 5,
        "R$100+",   6,  0
    )
```
After creating: select `CLV Band` column → Column tools → Sort by column → `CLV Band Sort`.

**Column 3: Churn Prob Bucket** (used in P3 histogram X-axis)
```dax
Churn Prob Bucket =
    SWITCH(
        TRUE(),
        CustomerHealth[churn_probability] < 0.1, "0.0–0.1",
        CustomerHealth[churn_probability] < 0.2, "0.1–0.2",
        CustomerHealth[churn_probability] < 0.3, "0.2–0.3",
        CustomerHealth[churn_probability] < 0.4, "0.3–0.4",
        CustomerHealth[churn_probability] < 0.5, "0.4–0.5",
        CustomerHealth[churn_probability] < 0.6, "0.5–0.6",
        CustomerHealth[churn_probability] < 0.7, "0.6–0.7",
        CustomerHealth[churn_probability] < 0.8, "0.7–0.8",
        CustomerHealth[churn_probability] < 0.9, "0.8–0.9",
        "0.9–1.0"
    )
```

**Column 4: Churn Prob Bucket Sort**
```dax
Churn Prob Bucket Sort =
    SWITCH(
        CustomerHealth[Churn Prob Bucket],
        "0.0–0.1", 1,  "0.1–0.2", 2,  "0.2–0.3", 3,
        "0.3–0.4", 4,  "0.4–0.5", 5,  "0.5–0.6", 6,
        "0.6–0.7", 7,  "0.7–0.8", 8,  "0.8–0.9", 9,
        "0.9–1.0", 10, 0
    )
```
Sort `Churn Prob Bucket` by `Churn Prob Bucket Sort`.

**Column 5: Is Churn Risk** (1/0 flag at deployed threshold)
```dax
Is Churn Risk =
    IF( CustomerHealth[churn_probability] >= 0.6, 1, 0 )
```

**Column 6: CLV Churn Quadrant** (used in P5 quadrant scatter)
```dax
CLV Churn Quadrant =
    SWITCH(
        TRUE(),
        CustomerHealth[churn_probability] >= 0.6
            && NOT ISBLANK( CustomerHealth[clv_predicted_6m] )
            && CustomerHealth[clv_predicted_6m] >= 0.93,  "Retain",
        CustomerHealth[churn_probability] < 0.6
            && NOT ISBLANK( CustomerHealth[clv_predicted_6m] )
            && CustomerHealth[clv_predicted_6m] >= 0.93,  "Grow",
        CustomerHealth[churn_probability] >= 0.6
            && ( ISBLANK( CustomerHealth[clv_predicted_6m] )
                 || CustomerHealth[clv_predicted_6m] < 0.93 ), "Reactivate",
        "Monitor"
    )
```
Note: 0.93 is the actual median CLV from your data. If `[Median CLV]` returns a different value after loading, update this literal to match.

**Column 7: Priority Sort** (sort action priority correctly in tables)
```dax
Priority Sort =
    SWITCH(
        CustomerHealth[latest_action_priority],
        "HIGH", 1,  "MED", 2,  "LOW", 3,  4
    )
```

### 2.3 — What-If parameter (required for P3)

The `WhatIf Actionable` and `WhatIf Retention Campaign` measures in the DAX script reference `'Threshold Parameter'[Threshold Parameter Value]`. Create this parameter before those measures will work:

1. Modeling tab → New parameter → Numeric range
2. Name: `Threshold Parameter`
3. Minimum: `0.2`, Maximum: `0.9`, Increment: `0.05`, Default: `0.6`
4. Click Add to report (Power BI adds a slicer automatically — you can delete this slicer from the page and replace it with a custom-styled one)
5. Power BI auto-creates the measure `[Threshold Parameter Value]` — do not rename it

Re-run the DAX script after creating the parameter if the WhatIf measures show errors.

---

## PART 3 — GLOBAL SETUP

### 3.1 — Page size (apply to all pages)

For every page: Format pane → Canvas settings → Type: Custom → Width: `1280` px, Height: `800` px.

View → Page view → Fit to page.

### 3.2 — Hide default page tabs

Right-click each page tab → Hide page. Navigation is handled entirely by the left rail buttons and bookmarks. Hiding page tabs forces all navigation through your designed system.

### 3.3 — Left navigation rail

This is the one piece of the build that touches every page twice: once now (skeleton, while no bookmarks exist yet) and once again after Part 5 (wiring, once they do). Trying to do it in one pass is the most common source of "my buttons don't go anywhere" — there's nothing to point them at yet. Two passes, in this order, fixes that.

**Why two passes:** a button's Action target is a *bookmark*, and the active-state indicator's visibility is *controlled by* a bookmark. But bookmarks aren't created until Part 5, and Part 5 itself depends on every page already existing (it saves visibility states *of* the visuals you're about to build). Build the rail's static shell now so every page has consistent navigation from the start, then come back in one short pass after Part 5 to wire the live pieces.

| Pass | When | What it covers |
|------|------|-----------------|
| **A — Skeleton** | Now, on P1, before any other visual | Rail background, title, 6 buttons with labels/position/style. Buttons exist but their Action field is left empty for now — see note in Step 3. |
| **B — Wiring** | After Part 5 (all bookmarks exist) | Point each button's Action at its target bookmark. Add and configure the 6 active-state indicator rectangles. Re-test by clicking through all 6. |

Do Pass A once, on P1, then replicate to every other page immediately (Step 5 below) so all 7 pages stay visually identical while you build. Do Pass B once, at the very end, on the single master copy, then replicate again. Replicating twice is intentional — it's faster than hand-editing 7 copies in place.

---

#### Pass A — Skeleton (build now, on P1 only)

**Step 1 — Rail background**

Insert → Shapes → Rectangle.

| Property | Value |
|---|---|
| Position | X=0, Y=0, W=140, H=800 |
| Fill | `#1E1C1A` (dark theme) / `#F0EEE9` (light theme) |
| Border | None |
| Shadow | None |

**Step 2 — Report title**

Insert → Text box.

| Property | Value |
|---|---|
| Text | "CRM Intelligence" |
| Position | X=10, Y=14, W=120, H=22 |
| Font | Segoe UI Semibold, 11px |
| Colour | Theme teal |

**Step 3 — Navigation buttons**

Six buttons, one per directly-navigable page. P2 (Customer 360) is drill-through only and is deliberately excluded — there is no button for it anywhere in the rail.

| # | Y | Label | Target page | Bookmark to assign in Pass B |
|---|-----|-------------------|------|------------------|
| 1 | 50 | ⬡ Command | P1 | `P1_Top5` |
| 2 | 98 | ⚡ Churn Risk | P3 | `P3_Operational` |
| 3 | 146 | ◎ Segments | P4 | `P4_RFM` |
| 4 | 194 | ◈ CLV | P5 | `P5_Default` |
| 5 | 242 | ◉ Geo Intel | P6 | `P6_GMV` |
| 6 | 290 | ✦ Sentiment | P7 | `P7_Default` |

For each button — Insert → Button → Blank:
- W=120, H=40, X=10, Y per the table above
- Font: Segoe UI, 11px. Default colour `#908D89`. Hover colour: theme teal.
- **Icon font caveat:** the glyphs above (⬡ ⚡ ◎ ◈ ◉ ✦) aren't part of the standard Segoe UI character set on every system and can fall back to a missing-glyph box when the file is opened on another machine, embedded in PowerPoint, or exported to PDF. Before committing to them, open the file on a second machine (or export to PDF) and confirm they render. If any glyph drops out, swap to Segoe MDL2 Assets icons (built into Windows, reliable in Power BI) or plain text labels with no icon — don't ship a rail with a visible tofu box.
- **Leave Format → Action off for now.** The target bookmarks don't exist yet (they're built per-page through Part 4, in build order P1 → P6 → P3 → P4 → P5 → P7). Setting the action now just means revisiting six buttons later anyway — skip straight to Pass B once Part 5 is done, and do it once.
- Do **not** add the active-state indicator rectangles in this pass. They depend on bookmarks too — they're built in Pass B (Step 4 below).

**Step 4 — Group and name**

Select the rail background, title, and all 6 buttons → right-click → Group → name the group `NavRail`.

**Step 5 — Replicate to every page**

Copy the `NavRail` group → paste onto P2 through P7, same X/Y on every page (pasting preserves position automatically — don't drag it).

At this point every page has identical, functional-looking navigation, but no button does anything yet and there's no indication of which page is active. That's expected and gets resolved in Pass B. Move on to Part 4 (page builds) now — don't block on Pass B.

---

#### Pass B — Wiring (after Part 5, once all 14 bookmarks exist)

Do this once, on P1's `NavRail` group, then re-replicate (it's faster than editing 7 separate copies).

**Step 1 — Wire the six buttons**

For each button: select it → Format pane → Action → toggle **On** → Type: **Bookmark** → pick the bookmark from the "Bookmark to assign" column in the Step 3 table above (each is that page's default/landing bookmark, not a secondary view like `P3_WhatIf` or `P6_Churn`).

**Step 2 — Build the active-state indicator (P1 first)**

One thin rectangle per page, shown only when that page's own bookmarks are active:
- Insert → Rectangle, 3px wide × 40px tall, teal fill, no border.
- Position it immediately left of that page's button (e.g., for the Command button at Y=50, place the indicator at X=6, Y=50).
- Selection pane → for this indicator, note its name (e.g., `Indicator_P1`).

**Step 3 — Set visibility per bookmark**

This is the step that's easy to get wrong: visibility is set *when you create or update the bookmark*, not on the shape itself. For every one of the 14 bookmarks in Part 5's inventory:
1. Apply the bookmark (click it in the Bookmarks pane so the page is in that state).
2. Selection pane → set the current page's `Indicator_PX` to **visible**, and hide the indicator shapes belonging to any other page if they happen to be on the same canvas (they normally aren't, since each page only carries its own indicator).
3. Right-click the bookmark → **Update** to save the visibility state into it.

Concretely: applying `P3_Operational` or `P3_WhatIf` should both leave `Indicator_P3` visible (both belong to P3) — but applying `P1_Top5` or `P1_Pareto` should leave `Indicator_P1` visible instead. The indicator tracks the *page*, not the specific secondary bookmark.

**Step 4 — Replicate the finished rail**

Once P1's rail is fully wired and its indicator confirmed working, re-group `NavRail` (now including the indicator) → copy → paste onto P2–P7, replacing the Pass-A skeleton copies. Each page keeps only its own indicator shape active per its own bookmarks — you don't need 6 indicators on every page, just the one matching that page.

**Step 5 — Test**

Click each of the 6 buttons from every page, including from a page you didn't start on. Confirm: (a) it lands on the right page, (b) exactly one indicator lights up and it's the correct one, (c) any slicer selections you'd made stay intact (this works because Part 5 instructs unchecking "Data" on these bookmarks — see Part 5 for why that matters).

---

### 3.4 — Data-as-of strip

Unlike the rail, this is a single repeating element, not a grouped system — add it directly to each page (or include it in the same group you copy in Step 5 above, since its position never changes).

Add a Card visual:
- Field: `[As Of Date Label]` measure from `_Measures`
- Position: X=150, Y=2, W=1110, H=18
- Font: Segoe UI 10px italic. Colour: `#7A7672`. Background: none. Border: none. Title: off.
- Displays automatically as "Data as of Oct 17, 2018 · ML cutoff 2018-05-01 · Churn window 180 days"

### 3.5 — Narrative card

Each page carries a 2-sentence static caption explaining what the page answers — the orientation a first-time business user needs before they start clicking. Position: X=150, Y=774, W=1110, H=22 on every page.
- Insert → Text box (plain static text, not a measure-driven Card)
- Font: 10px, colour `#7A7672`. Background: none.
- Per-page wording is given at the top of each page's section in Part 4.

### 3.6 — Colour encoding — apply consistently on every page

**Segment palette** — apply manually to every visual that shows `rfm_segment`. In Format → Data colours → "+" to add per-value overrides:

| Segment | Dark hex | Light hex |
|---------|----------|-----------|
| Champions | `#1FC990` | `#0D8A5E` |
| Loyal | `#7B74E8` | `#5F59CC` |
| Potential Loyalist | `#4BA3E8` | `#2876C8` |
| Frequent Low-Spender | `#F5A623` | `#D97B10` |
| Needs Attention | `#F2D06B` | `#B89310` |
| At Risk | `#E05C3A` | `#C44A27` |
| Can't Lose | `#D4537E` | `#B94476` |
| Hibernating | `#8A8582` | `#767371` |
| Lost | `#C5C2BD` | `#A8A5A1` |

**Action type colours:**

| Action | Dark | Light |
|--------|------|-------|
| RETENTION_CAMPAIGN | `#E05C3A` | `#C44A27` |
| REACTIVATION | `#F5A623` | `#D97B10` |
| MONITOR | `#8A8582` | `#767371` |

**Health tier colours:**

| Tier | Dark | Light |
|------|------|-------|
| High | `#1FC990` | `#0D8A5E` |
| Medium | `#F5A623` | `#D97B10` |
| Low | `#E05C3A` | `#C44A27` |

---

## PART 4 — PAGE BUILD SPECIFICATIONS

**Canvas work area** (after nav rail): X=150 to X=1260, Y=24 to Y=770. Working area: 1110 × 746 px.  
**Build order: P1 → P6 → P3 → P4 → P5 → P7 → P2.**

---

## P1 — COMMAND CENTRE

**User:** CEO, Head of Retention. First page opened.  
**Narrative card text:** "This page gives a portfolio-level snapshot of customer health, churn exposure, and action priority. Use state and date slicers to drill into any segment."

---

### P1-V1 — KPI strip (5 cards, top row)

**Visual type:** 5× Card  
**Layout:** Y=28, each card W=210, H=108, gap=8. X=150, 368, 586, 804, 1022.

| # | Field | Format | Label | Background |
|---|-------|--------|-------|------------|
| 1 | `[Total GMV]` | `"R$"#,0` | Total GMV (BRL) | Default (theme white/dark) |
| 2 | `[Total Customers]` | `#,0` | Unique customers | Default |
| 3 | `[Churn Rate]` | `0.0%` | Churn rate (rule-based, 180d) | Alert: coral at 10% transparency |
| 4 | `[HIGH Priority Count]` | `#,0` | HIGH-priority actions | Alert: coral at 10% transparency |
| 5 | `[Avg Health Score]` | `0.0` | Avg customer health (0–100) | Success: teal at 10% transparency |

**FORMAT SETTINGS (all 5 cards):**
- Callout value: Segoe UI Semibold, 28px, theme primary text colour
- Category label: 11px, muted (`#7A7672` dark / `#908D89` light)
- Visual border: on, 0.5px, `#2C2C38` dark / `#E0DDD8` light
- Corner radius: 8
- Title: off
- Padding: 16px

---

### P1-V2 — Action queue donut (top-right)

**Visual type:** Donut chart  
**Position:** X=880, Y=152, W=370, H=290

**PRE-CONDITION:** `CustomerHealth[latest_action_type]` must have 0 NULL rows. Verify: `COUNTROWS(FILTER(CustomerHealth, ISBLANK(CustomerHealth[latest_action_type])))` = 0 in DAX Query View.

**FIELD DROPS:**
- Legend: `CustomerHealth[latest_action_type]`
- Values: `[Total Customers]`
- Tooltips: `[Total Customers]`, `[Flagged GMV]`

**FORMAT SETTINGS:**
- Inner radius: 55%
- Data colours: RETENTION_CAMPAIGN=coral, REACTIVATION=amber, MONITOR=gray (manual override)
- Detail labels: on. Contents: Category, Percent. Font 11px. Position: Outside.
- Legend: off
- Title: off
- Centre overlay: add a Text Box at the visual's centre. Text: "38,531". Font: Segoe UI Semibold 18px. Below it: another text box "flagged". Font: 11px, muted. These two text boxes float over the donut centre — align manually.
- Tooltip: Add `[Flagged GMV]` to the tooltip fields well

**INTERACTION:** Right-click on this visual → set cross-filtering to P1-V5 (state bar) and the monthly trend.

**DRILL-THROUGH:** right-click a donut segment → Drill through → P3 (Churn & Action Risk) filtered to that action type.

---

### P1-V3 — Priority table (below donut)

**Visual type:** Table  
**Position:** X=880, Y=450, W=370, H=112

**FIELD DROPS:**
1. `CustomerHealth[latest_action_priority]` — sort by `CustomerHealth[Priority Sort]`
2. `[Total Customers]` — format `#,0` — title "Customers"
3. `[Flagged GMV]` — format `"R$"#,0` — title "GMV at stake"

**FORMAT SETTINGS:**
- Header: bg `#1E1C1A` dark / `#1E1C1A` light, text white, Segoe UI Semibold 11px
- Conditional formatting on priority column background: HIGH→coral, MED→amber, LOW→gray (use rules: value = "HIGH" → coral, etc.)
- Row height: 34px. Grid: horizontal lines only, 0.5px.
- Title: off. Border: on.
- Sort: by `Priority Sort` ascending (HIGH first)

---

### P1-V4 — Monthly GMV trend (left, main)

**Visual type:** Area chart  
**Position:** X=150, Y=152, W=716, H=286  
**Source table:** `MonthlyGMV`

**FIELD DROPS:**
- X-axis: `MonthlyGMV[order_month]` — set hierarchy level to Month only
- Y-axis: `MonthlyGMV[monthly_gmv]`
- Tooltips: `MonthlyGMV[order_count]`

**FORMAT SETTINGS:**
- Area fill: teal at 20% transparency. Line: teal, 2px solid.
- X-axis: Year+Month format. Font 11px. Gridlines: off.
- Y-axis: title off. Format `"R$"#,0,,,"M"`. Gridlines: 0.5px muted.
- Title: off.
- Analytics pane → Constant line: value = `43221` (Excel date serial for 2018-05-01, or use the date value that corresponds to May 2018 in your date axis). Colour: coral, dashed 1px. Label: "ML cutoff · May 2018". Position: Behind.
- Add a Text Box above the reference line: "← Train" (left side) and "Validate →" (right side). Font 10px, muted. These are static annotations, not chart labels.

**INTERACTION:** Cross-filters P1-V2 (donut) and P1-V5 (state bar) when a date range is brushed.

---

### P1-V5 — Health tier donut (bottom-left)

**Visual type:** Donut chart  
**Position:** X=150, Y=450, W=346, H=302

**FIELD DROPS:**
- Legend: `CustomerHealth[health_tier]`
- Values: `[Total Customers]`

**FORMAT SETTINGS:**
- Data colours: High=teal, Medium=amber, Low=coral
- Inner radius: 50%
- Detail labels: Category, Percent. Font 11px.
- Legend: bottom, font 11px.
- Title: "Customer health tier distribution", 13px bold, left.

---

### P1-V6 — Top states bar (bottom-right)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=510, Y=450, W=556, H=302

**FIELD DROPS:**
- Y-axis: `GeoPerformance[customer_state]`
- X-axis: `GeoPerformance[total_gmv]`
- Tooltips: `GeoPerformance[customer_count]`, `GeoPerformance[churn_rate_pct]`, `GeoPerformance[pct_of_total_gmv]`

**FORMAT SETTINGS:**
- Top N filter (visual level): show Top 5 by `total_gmv`
- Sort: X descending
- Data labels: on, inside end, white, 11px, format `"R$"#,0,,,"M"`
- X-axis: off. Y-axis: 12px.
- Single colour: teal.
- Title: "Top 5 states by GMV", 13px bold left.
- Conditional formatting on bars: apply state-level churn threshold — if state churn rate > 73%, add a small coral dot annotation (not directly possible natively; workaround: use a second table visual overlaid showing only churn rate for the same 5 states, formatted as a conditional colour swatch — optional).

**INTERACTION:** Click a state bar → cross-filters P1-V2 (donut) and P1-V4 (trend). Right-click → Drill through → P6 (Geo Intelligence).

---

### P1 — Anomaly outliers table

**Visual type:** Table  
**Position:** X=880, Y=576, W=370, H=186  
**Label (text box above):** "High-risk, low-value outliers" in coral 12px bold

**Purpose:** Customers with top-5% churn probability but bottom-20% CLV — unexpected combination, worth manual investigation.

**FIELD DROPS:**
1. `CustomerHealth[customer_unique_id]` — title "Customer ID"
2. `CustomerHealth[churn_probability]` — format `0.00%` — title "Churn prob"
3. `CustomerHealth[clv_predicted_6m]` — format `"R$"0.00` — title "Pred CLV"
4. `CustomerHealth[rfm_segment]` — title "Segment"
5. `CustomerHealth[customer_state]` — title "State"

**Visual-level filters:**
- `churn_probability` is Top N: Top 5% → set filter type to "Advanced filtering" → value ≥ 0.92 (verify with `PERCENTILEINC` in Query View first — run `EVALUATE ROW("p95", PERCENTILEINC(CustomerHealth[churn_probability], 0.95))`)
- `clv_predicted_6m` ≤ 0.37 (bottom 20% — verify with `PERCENTILEINC(CustomerHealth[clv_predicted_6m], 0.2)`)

**FORMAT SETTINGS:**
- Sort: `churn_probability` descending
- Conditional bg on `churn_probability`: gradient white→coral
- Header: same dark header style as all tables
- Row height: 30px
- Title: off (title is the text box above)
- Row click → drill-through to P2 (Customer 360)

---

### P1 — Bookmarks

**P1_Top5 (default):** top-5 state bar visible. Pareto overlay hidden.
**P1_Pareto:** state bar shows all 27 states (remove Top N filter). A second line series on P1-V6 shows cumulative GMV percentage (calculated column on `GeoPerformance`: `Cumulative GMV Pct = DIVIDE(CALCULATE(SUM(GeoPerformance[total_gmv]), TOPN(RANKX(ALL(GeoPerformance), GeoPerformance[total_gmv]), GeoPerformance, GeoPerformance[total_gmv])), [National GMV], 0)`). Add reference line at 80%. Add a Y2 axis for the cumulative line.

Buttons: "Top 5 States" → P1_Top5. "Pareto view" → P1_Pareto. Position: X=510, Y=750.

---

## P2 — CUSTOMER 360

**User:** Drill-through target. Not directly navigable.  
**Access:** Right-click any customer row on any page → Drill through → Customer 360.  
**Narrative card text:** "Single-customer account view. All metrics filtered to the customer drilled into. Use the back button to return."

**SOURCE TABLES USED:** `CustomerHealth` (all columns from `vw_customer_health`), `SentimentScores`, `ActionQueue`, `OrderTimeline` (joined via relationship on `customer_unique_id`).

**Setup — configure as drill-through destination:**
1. Go to P2.
2. Visualizations pane → scroll to bottom → Drill through section.
3. Drag `CustomerHealth[customer_unique_id]` into "Add drill-through fields here".
4. This makes P2 a drill-through target. Power BI auto-adds a back button — format it to match your style (see P2-Back Button below).

---

### P2 — Layout overview

**Left sidebar** (X=150, Y=28, W=278, H=746): customer identity card stack  
**Right top** (X=440, Y=28, W=810, H=180): order timeline strip  
**Right middle-left** (X=440, Y=220, W=398, H=158): 4 metric cards  
**Right middle-right** (X=848, Y=220, W=402, H=158): delivery + review profile  
**Right mid-lower** (X=440, Y=390, W=810, H=148): sentiment strip  
**Right bottom** (X=440, Y=550, W=810, H=100): trigger reason callout  
**Back button** (X=150, Y=740, W=278, H=34): return navigation

---

### P2-V1 — Left sidebar identity card

Background rectangle: X=150, Y=28, W=278, H=746. Fill: `#1A1A22` dark / `#F0EEE9` light. Corner radius 8.

Stack these visuals inside the sidebar (top to bottom):

**Customer ID** — Card. Field: `FIRSTNONBLANK(CustomerHealth[customer_unique_id], 1)`. Font 11px, colour muted. Label "Customer ID" 10px. Position: X=162, Y=40, W=254, H=44.

**Location** — Card. Measure: `FIRSTNONBLANK(CustomerHealth[customer_city], 1) & ", " & FIRSTNONBLANK(CustomerHealth[customer_state], 1)`. Font 12px. Label "Location" 10px. Position: X=162, Y=92, W=254, H=36.

**Health score gauge** — Gauge visual. Value: `[Avg Health Score]`. Min=0, Max=100, Target=75. Arc colour: teal. Position: X=162, Y=136, W=254, H=130. Callout font: 22px Segoe UI Semibold. Target label: "High tier".

**Health tier badge** — Card. Field: `FIRSTNONBLANK(CustomerHealth[health_tier], 1)`. Conditional bg: High→teal@15%, Medium→amber@15%, Low→coral@15%. Position: X=162, Y=274, W=254, H=40. Label "Health tier" 10px.

**RFM segment** — Card. Field: `FIRSTNONBLANK(CustomerHealth[rfm_segment], 1)`. Conditional bg: apply segment colours at 20% transparency. Position: X=162, Y=322, W=254, H=40. Label "RFM segment" 10px.

**Action + priority** — two Cards side by side.
- Action: `FIRSTNONBLANK(CustomerHealth[latest_action_type], 1)`. X=162, Y=370, W=122, H=40. Bg: action type colours.
- Priority: `FIRSTNONBLANK(CustomerHealth[latest_action_priority], 1)`. X=292, Y=370, W=124, H=40. Bg: HIGH→coral, MED→amber, LOW→gray.

**Days since last order** — Card. Field: `FIRSTNONBLANK(CustomerHealth[days_since_last_order], 1)`. Format: `#,0 "days"`. Conditional callout colour: value > 180 → coral, else teal. Position: X=162, Y=418, W=254, H=56. Label "Days since last order" 10px.

**Churn probability** — Card. Field: `FIRSTNONBLANK(CustomerHealth[churn_probability], 1)`. Format: `0.0%`. Callout font: 32px Segoe UI Semibold. Conditional colour: ≥ 0.6 → coral, < 0.6 → teal. Position: X=162, Y=482, W=254, H=82. Label "Churn probability (model)" 10px.

**CLV prediction** — Multi-row card or 3 narrow Cards. Fields: `clv_predicted_6m` (format `"R$"0.00`, label "Predicted CLV (6m)"), `clv_ci_lower` (label "CI lower"), `clv_ci_upper` (label "CI upper"). Position: X=162, Y=572, W=254, H=80. If using one card: measure = `"R$" & FORMAT(FIRSTNONBLANK(CustomerHealth[clv_predicted_6m],1),"0.00") & " (" & FORMAT(FIRSTNONBLANK(CustomerHealth[clv_ci_lower],1),"0.00") & " – " & FORMAT(FIRSTNONBLANK(CustomerHealth[clv_ci_upper],1),"0.00") & ")"`.

**Back button** — Insert → Button → Back. X=162, Y=706, W=254, H=34. Label "← Back to previous page". Font 12px. Border 0.5px muted.

---

### P2-V2 — Order timeline

**Visual type:** Scatter chart  
**Position:** X=440, Y=28, W=810, H=180  
**Source:** `OrderTimeline` (imported in Part 0.5)

**FIELD DROPS:**
- X-axis: `OrderTimeline[days_from_start]`
- Y-axis: `OrderTimeline[review_score]`
- Size: `OrderTimeline[total_order_gmv]`
- Legend: `OrderTimeline[sentiment_label]`
- Play Axis: off
- Tooltips: `order_date`, `total_order_gmv`, `delivery_delta_days`, `review_score`, `sentiment_label`

**FORMAT SETTINGS:**
- Marker colours: positive=teal, neutral=gray, negative=coral. Null sentiment=muted gray.
- Marker size: min 6, max 18.
- X-axis: title off. Range covers the full dataset (days since Sept 2016). Labels show date-equivalent month markers (add a calculated column in OrderTimeline: `Month Label = FORMAT(DATEADD('2016-09-01', [days_from_start], DAY), "MMM YY")`).
- Y-axis: title off. Range 0–5.5. Labels: 1/2/3/4/5.
- Gridlines: horizontal only, 0.5px.
- Analytics: Constant line at Y=3 (below-average review). Colour muted dashed.
- Title: "Order history — each dot = one order, colour = sentiment, size = GMV", 11px, left.
- Legend: off (add manual text labels as text boxes for the 3 colours).

**INTERACTION:** Disabled. This visual is read-only — it shows data for the drilled-through customer only.

---

### P2-V3 — 4 metric tiles

**Visual type:** 4× Card in 2×2 grid  
**Position:** X=440, Y=220, W=398, H=158  
Each card W=190, H=72. Gap 8px.

| Card | Field | Format | Label |
|------|-------|--------|-------|
| 1 | `FIRSTNONBLANK(CustomerHealth[total_gmv], 1)` | `"R$"#,0` | Total GMV |
| 2 | `FIRSTNONBLANK(CustomerHealth[total_orders], 1)` | `#,0` | Orders |
| 3 | `FIRSTNONBLANK(CustomerHealth[avg_order_value], 1)` | `"R$"#,0` | Avg order value |
| 4 | `FIRSTNONBLANK(CustomerHealth[total_freight_paid], 1)` | `"R$"#,0` | Freight paid |

---

### P2-V4 — Delivery + review profile

**Visual type:** 3× Card  
**Position:** X=848, Y=220, W=402, H=158  
Each card W=124, H=72. Gap 8px.

| Card | Field | Format | Label | Colour rule |
|------|-------|--------|-------|-------------|
| 1 | `FIRSTNONBLANK(CustomerHealth[avg_delivery_delta_days], 1)` | `0.0 "d"` | Avg delivery delta | > 0 → coral (late), ≤ 0 → teal (early) |
| 2 | `FIRSTNONBLANK(CustomerHealth[pct_late_deliveries], 1)` | `0.0%` | % late deliveries | > 0.1 → coral |
| 3 | `FIRSTNONBLANK(CustomerHealth[pct_negative_reviews], 1)` | `0.0%` | % negative reviews | > 0.2 → coral |

---

### P2-V5 — Sentiment strip

**Visual type:** Scatter chart  
**Position:** X=440, Y=390, W=810, H=148  
**Source:** `SentimentScores` (filtered to the drill-through customer via relationship)

**FIELD DROPS:**
- X-axis: `SentimentScores[compound_score]`
- Y-axis: add calculated column to SentimentScores: `Y_Pos = 1` (constant — puts all dots on one horizontal line)
- Legend: `SentimentScores[sentiment_label]`
- Tooltips: `review_score`, `compound_score`, `sentiment_label`, `review_creation_date`, `review_comment_message` (truncated in tooltip)

**FORMAT SETTINGS:**
- Colours: positive=teal, neutral=gray, negative=coral.
- X-axis: range -1 to 1. Gridlines off. Labels at -1, -0.05, 0, 0.05, 1.
- Analytics: Constant lines at -0.05 (coral dashed) and +0.05 (teal dashed) — the LeIA neutral-zone boundaries.
- Y-axis: off.
- Marker size: 10px fixed.
- Title: "Review sentiment (each dot = one review with text)", 11px, left.

---

### P2-V6 — Trigger reason callout

**Visual type:** Card  
**Position:** X=440, Y=550, W=810, H=100

**FIELD:** `FIRSTNONBLANK(CustomerHealth[latest_action_reason], 1)`

**FORMAT SETTINGS:**
- Background: amber at 15% transparency.
- Left border simulation: add a 4px-wide teal/amber/coral rectangle on the left edge of this card (based on action priority).
- Callout font: 13px. Wrap text on.
- Category label: "Why this action was assigned", 10px muted, shown above.
- Title: off.
- This shows text like: "Churn risk 0.73 ≥ 0.60; CLV R$342.10 at 68th pct (≥ 50th) — high-value customer, premium retention warranted"

---

## P3 — CHURN & ACTION RISK

**User:** Retention manager. Daily operational — "who do I contact today?"  
**Narrative card text:** "Ranked list of customers by churn urgency. Click any row to see the full customer profile. Use the What-If panel to test how changing the threshold affects your workload."

---

### P3-V1 — Churn KPI strip (4 cards, top row)

**Visual type:** 4× Card  
**Position:** Y=28, each W=266, H=90, gap=8. X=150, 424, 698, 972.

| # | Field | Format | Label | Bg |
|---|-------|--------|-------|-----|
| 1 | `[Churn Rate]` | `0.0%` | Rule-based churn rate (180d) | Coral@10% |
| 2 | `[Avg Churn Probability]` | `0.00` | Model avg churn probability | Coral@10% |
| 3 | `[HIGH Priority Count]` | `#,0` | HIGH-priority customers | Coral@10% |
| 4 | `[Flagged GMV]` | `"R$"#,0,,,"M"` | Flagged GMV at stake | Coral@10% |

---

### P3-V2 — Ranked customer table (left, main)

**Visual type:** Table  
**Position:** X=150, Y=130, W=618, H=632  
**Source:** `ChurnSignals`

**FIELD DROPS (columns in order):**

| # | Field | Title | Width | Format | Conditional formatting |
|---|-------|-------|-------|--------|----------------------|
| 1 | `ChurnSignals[customer_unique_id]` | Customer ID | 160px | text | none |
| 2 | `ChurnSignals[churn_probability]` | Churn prob | 90px | `0.000` | Data bars, coral fill |
| 3 | `ChurnSignals[urgency_score]` | Urgency | 70px | `0.0` | Bg gradient white→coral |
| 4 | `ChurnSignals[latest_action_type]` | Action | 130px | text | Bg: action type colours |
| 5 | `ChurnSignals[primary_driver]` | Driver | 110px | text | none |
| 6 | `ChurnSignals[clv_predicted_6m]` | Pred CLV | 80px | `"R$"0.00` | none |
| 7 | `ChurnSignals[days_since_last_order]` | Days idle | 60px | `#,0` | none |

**FORMAT SETTINGS:**
- Default sort: `urgency_score` descending (click column header after loading)
- Top N visual filter: `urgency_score` Top 500
- Header: bg `#1E1C1A`, text white, Segoe UI Semibold 11px
- Alternating rows: default / `#F5F4F1` (light) or `#191920` (dark)
- Row height: 36px
- Grid: horizontal 0.5px only
- High-churn row highlight: Format → Conditional formatting → Background colour → Rules: `churn_probability > 0.8` → coral at 8% transparency applied to all columns. (Power BI applies row-level bg via per-column rules — set the same rule on all 7 columns.)
- Title: off

**INTERACTION:** Row click → drill-through to P2. This table cross-filters P3-V3, P3-V4, P3-V5.

---

### P3-V3 — Churn driver breakdown (top-right)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=780, Y=130, W=470, H=200  
**Source:** `ChurnSignals`

**FIELD DROPS:**
- Y-axis: `ChurnSignals[primary_driver]`
- X-axis: `[Total Customers]`

**FORMAT SETTINGS:**
- Sort: X descending
- Colour: single coral
- Data labels: on, inside end, white, 11px
- X-axis: off. Y-axis: 12px.
- Title: "Primary churn driver", 13px bold left

---

### P3-V4 — Churn probability histogram (middle-right)

**Visual type:** Clustered column chart  
**Position:** X=780, Y=342, W=470, H=232  
**Source:** `CustomerHealth`

**FIELD DROPS:**
- X-axis: `CustomerHealth[Churn Prob Bucket]` (sorted by `Churn Prob Bucket Sort`)
- Y-axis: `[Total Customers]`

**FORMAT SETTINGS:**
- Colour rules (conditional formatting → Rules on bar colour):
  - `Churn Prob Bucket Sort` ≤ 3 → gray (below threshold zone)
  - `Churn Prob Bucket Sort` ≥ 4 → coral (at or above threshold zone)
- Analytics → Constant line: value = 3.5 (visually between the 0.2–0.3 and 0.3–0.4 buckets). Colour coral dashed. Label "Deployed threshold (0.299)".
- X-axis: labels rotated 30°, 10px. Title off.
- Y-axis: title off. Gridlines 0.5px.
- Data labels: off.
- Title: "Churn probability distribution", 13px bold left

---

### P3-V5 — Segment × churn rate (bottom-right)

**Visual type:** Table  
**Position:** X=780, Y=586, W=470, H=176  
**Source:** `CustomerHealth`

**FIELD DROPS:**
1. `CustomerHealth[rfm_segment]`
2. `[Segment Churn Rate]` — format `0.0%` — title "Churn %"
3. `[Total Customers]` — format `#,0` — title "Customers"

**FORMAT SETTINGS:**
- Sort: `Segment Churn Rate` descending
- Conditional bg on `rfm_segment`: apply segment colours at 25% transparency
- Header: dark header style
- Row height: 28px
- Title: "Churn rate by segment", 13px bold

---

### P3 — What-If action simulator (hidden panel, toggled by bookmark)

**PRE-CONDITION:** The `Threshold Parameter` must be created first (see Part 2.3). The measures `[WhatIf Actionable]`, `[WhatIf Retention Campaign]`, `[WhatIf Reactivation]`, `[WhatIf Delta Actionable]`, `[WhatIf Delta Label]` must exist in `_Measures`.

**Build the panel:**

Position: X=780, Y=130, W=470, H=658. This panel overlaps exactly where P3-V3, P3-V4, P3-V5 sit — it replaces them when active.

1. **Threshold slicer** — the auto-created `Threshold Parameter` slicer. Format: Slider style. Position: X=790, Y=140, W=450, H=60. Label "Churn threshold". Remove default slicer title; add a text box label above it instead.

2. **Current vs simulated comparison** — 4 Cards in 2×2 grid. X=790, Y=210, W=450, H=160:
   - `[Actionable Customers]` — label "Current actionable (threshold 0.60)"
   - `[WhatIf Actionable]` — label "Simulated actionable"
   - `[HIGH Priority Count]` — label "Current RETENTION CAMPAIGN"
   - `[WhatIf Retention Campaign]` — label "Simulated RETENTION CAMPAIGN"

3. **Delta card** — Card at X=790, Y=378, W=450, H=60. Field: `[WhatIf Delta Label]`. Conditional colour: positive delta → coral (more customers flagged), negative → teal.

4. **GMV impact** — 2 Cards at X=790, Y=446, W=220, H=60 and X=1018, Y=446, W=222, H=60:
   - `[Flagged GMV]` — label "Current flagged GMV"
   - A simulated GMV measure: `WhatIf Flagged GMV = CALCULATE(SUM(CustomerHealth[total_gmv]), CustomerHealth[churn_probability] >= 'Threshold Parameter'[Threshold Parameter Value])` — label "Simulated flagged GMV"

5. **Annotation** — Text box at X=790, Y=514, W=450, H=80. Text: "Moving the slider simulates the business impact of changing the ML threshold. Higher threshold = fewer but more certain at-risk customers. Lower threshold = broader net but more false positives. The model was deployed at 0.299 (F1-optimised). 0.6 is the action-rule cutoff." Font 11px muted.

**Bookmarks:**
- `P3_Operational`: panel hidden (Selection pane: hide all 5 panel elements). P3-V3, V4, V5 visible. Button "What-If simulator ↗" visible.
- `P3_WhatIf`: panel visible. P3-V3, V4, V5 hidden. Button "← Operational view" visible inside panel area.

**Buttons:**
- "What-If simulator ↗" at X=780, Y=750, W=220, H=28 → triggers `P3_WhatIf`
- "← Operational view" at X=780, Y=750, W=220, H=28 (same position, different bookmark state) → triggers `P3_Operational`

---

## P4 — SEGMENTATION & RFM

**User:** CRM analyst. Segment strategy, campaign planning.  
**Narrative card text:** "Customer segments by RFM behaviour. Use the toggle to switch between rule-based labels and K-means clusters. Click a segment in the scatter to filter all visuals to that group."

---

### P4-V1 — RFM scatter (left, primary)

**Visual type:** Scatter chart  
**Position:** X=150, Y=28, W=618, H=454  
**Source:** `RFMAggregated` (aggregated query — 125 rows maximum, NOT individual customers)

**PERFORMANCE NOTE:** Never drop `customer_unique_id` into the Details well on this visual. 96,096 individual dots will make Power BI unresponsive. The `RFMAggregated` query pre-groups by the 5×5×5 score combinations, giving at most 125 data points with a `customer_count` column for bubble sizing.

**FIELD DROPS:**
- X-axis: `RFMAggregated[recency_score]`
- Y-axis: `RFMAggregated[monetary_score]`
- Size: `RFMAggregated[customer_count]`
- Legend: `RFMAggregated[rfm_segment]`
- Play Axis: off
- Details: `RFMAggregated[rfm_segment]` (same as legend — enables tooltip page)
- Tooltips: `avg_gmv`, `avg_churn_prob`, `avg_health_score`, `customer_count`, `frequency_score`

**FORMAT SETTINGS:**
- Marker size: min 6, max 28
- Data colours: apply segment palette (Part 3.6)
- X-axis: title "Recency score (1=oldest · 5=most recent)". Range 0.5–5.5. Font 11px.
- Y-axis: title "Monetary score (1=lowest · 5=highest)". Range 0.5–5.5. Font 11px.
- Gridlines: both axes, 0.5px muted.
- Legend: position right, font 11px.
- Title: off.
- Tooltip page: Format → Tooltip → Type: Report page → "RFM Tooltip" (build this page — see below).

**INTERACTION:** Bubble click → cross-filters P4-V2, P4-V3, P4-V4. Right-click → Drill through → P3 (Churn Risk) filtered to that segment.

---

### P4 — RFM Tooltip page

Create a new page. Right-click the page tab → Page information → enable "Allow Ctrl+Click" = off. Format → Canvas settings → Type: Tooltip → W=300, H=200.

Contents (all cards, no titles):
- `RFMAggregated[rfm_segment]` — font 14px Semibold, teal
- `RFMAggregated[customer_count]` — label "Customers"
- `RFMAggregated[avg_gmv]` — format `"R$"#,0` — label "Avg GMV"
- `RFMAggregated[avg_churn_prob]` — format `0.0%` — label "Avg churn prob"
- `RFMAggregated[avg_health_score]` — format `0.0` — label "Avg health score"

Background: `#1E1E26` (dark) / `#FFFFFF` (light). Padding 12px.

On P4-V1: Format → Tooltip → Type: Report page → select "RFM Tooltip".

---

### P4-V2 — Segment × action heatmap (bottom-left)

**Visual type:** Matrix  
**Position:** X=150, Y=494, W=618, H=278

**FIELD DROPS:**
- Rows: `CustomerHealth[rfm_segment]`
- Columns: `CustomerHealth[latest_action_type]`
- Values: `[Total Customers]`

**FORMAT SETTINGS:**
- Cell background conditional: colour scale, white (low) → teal (high). Apply to Values cells.
- High-value font override: conditional font colour — if `[Total Customers]` > 5000 → white text, else dark. (Set via conditional formatting → Font colour → Rules.)
- Row subtotals: off. Column subtotals: off.
- Header: bg dark, text white, Segoe UI Semibold 11px.
- Row height: 30px.
- Title: "Segment × Action assignment cross-tab", 13px bold left.

---

### P4-V3 — Segment distribution bar (right-panel, top)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=780, Y=28, W=470, H=354

**FIELD DROPS:**
- Y-axis: `CustomerHealth[rfm_segment]`
- X-axis: `[Total Customers]`
- Tooltips: `[Total GMV]`, `[Avg Health Score]`, `[Segment Churn Rate]`

**FORMAT SETTINGS:**
- Sort: `[Total Customers]` descending
- Data colours: segment palette (per-segment override via Format → Data colours → "+")
- Data labels: on, inside end, white, 11px, format `#,0`
- X-axis: off. Y-axis: 12px.
- Title: "Customer distribution by segment", 13px bold left.
- Add a second measure series for segment %: `[Segment Pct] = DIVIDE([Total Customers], CALCULATE([Total Customers], ALL(CustomerHealth[rfm_segment])), 0)`. Add as a secondary Y-axis line series with data labels showing percentage. This gives count + % in one visual.

---

### P4-V4 — Small multiples 2×2 grid (right-panel, bottom)

**Position:** X=780, Y=394, W=470, H=378. Each chart W=225, H=181, gap 8px.

**Chart A — Avg GMV by segment** (X=780, Y=394):
- Horizontal bar. Y: `rfm_segment`. X: `[Avg GMV per Customer]`. Format `"R$"#,0`. Segment colours. Title: "Avg GMV / customer", 11px.

**Chart B — Avg health score by segment** (X=1013, Y=394):
- Horizontal bar. Y: `rfm_segment`. X: `[Avg Health Score]`. Format `0.0`. Conditional bar colour: > 75 → teal, 50–75 → amber, < 50 → coral (use conditional formatting rules). Title: "Avg health score", 11px.

**Chart C — K-means cluster donut** (X=780, Y=583):
- Donut. Legend: `CustomerHealth[km_cluster]`. Values: `[Total Customers]`. Detail labels: category + count. No legend. Below: Text box "K=5 · Silhouette=0.336 · Cluster IDs are numeric labels, not business segments". Font 9px muted. Title: "K-means clusters (K=5)", 11px.

**Chart D — Segment GMV share treemap** (X=1013, Y=583):
- Treemap. Group: `CustomerHealth[rfm_segment]`. Values: `[Total GMV]`. Data labels: on, percentage. Segment colours. Title: "GMV share by segment", 11px.

---

### P4 — RFM / K-means toggle bookmarks

**P4_RFM (default):**
- P4-V1 legend = `RFMAggregated[rfm_segment]`
- P4-V3 sorted by `[Total Customers]`
- Text box "RFM Rule-Based Segmentation" visible
- Text box "K-means Cluster View" hidden

**P4_KMeans:**
- P4-V1: swap the Legend field to `CustomerHealth[km_cluster]`. (This requires building two scatter visuals on P4 — one with rfm_segment legend, one with km_cluster legend — and toggling their visibility via bookmarks.)
- P4-V3: add a second bar chart showing cluster sizes, toggle visibility
- Text boxes swapped

Buttons: "RFM segments" and "K-means clusters" at X=780, Y=758. Each W=228, H=28. Triggers respective bookmark.

---

## P5 — CLV & PREDICTED VALUE

**User:** Revenue forecasting, VP Retention.  
**Narrative card text:** "Predicted 6-month CLV for 71,186 customers (74%). Values are low because 99.2% of customers had zero post-cutoff spend — the model predicts behavioural propensity, not absolute revenue."

---

### P5-V1 — CLV KPI strip (4 cards, top)

**Position:** Y=28, each W=265, H=90, gap=8. X=150, 423, 696, 969.

| # | Field | Format | Label |
|---|-------|--------|-------|
| 1 | `[CLV Coverage Pct]` | `0.0%` | Customers with CLV prediction |
| 2 | `[Median CLV]` | `"R$"0.00` | Median predicted CLV (6m) |
| 3 | `[Max CLV]` | `"R$"0.00` | Max predicted CLV (6m) |
| 4 | `[Total CLV Portfolio]` | `"R$"#,0` | Total CLV portfolio (6m sum) |

**CLV disclaimer strip** — Text box at X=150, Y=120, W=1110, H=22:  
"ⓘ CLV values are structurally low — 99.2% of customers had zero post-cutoff spend due to the May–Oct 2018 validation window. Values reflect behavioural propensity. See documentation."  
Font 10px italic, muted. Background amber at 8% transparency. No border.

---

### P5-V2 — CLV distribution histogram (left, main)

**Visual type:** Clustered column chart  
**Position:** X=150, Y=152, W=558, H=340  
**Source:** `CustomerHealth`

**FIELD DROPS:**
- X-axis: `CustomerHealth[CLV Band]` (sort by `CLV Band Sort`)
- Y-axis: `[Total Customers]`

**FORMAT SETTINGS:**
- Conditional bar colours (Format → Data colours → Rules):
  - `CLV Band Sort` = 0 → gray `#8A8582` (No CLV)
  - `CLV Band Sort` = 1 → muted gray (R$0)
  - `CLV Band Sort` = 2 → amber
  - `CLV Band Sort` = 3 → theme teal
  - `CLV Band Sort` = 4 → green
  - `CLV Band Sort` = 5 → dark green
  - `CLV Band Sort` = 6 → dark green (R$100+)
- X-axis: labels rotated 30°, 10px. Title: "Predicted CLV band (6-month)".
- Y-axis: format `#,0`. Title: "Customers". Gridlines 0.5px.
- Data labels: on, top, 10px.
- Title: "CLV distribution by predicted value band", 13px bold left.
- Add text box annotation: "Log-equivalent bucketing — the R$0.01–1 bucket contains the largest number of customers with a non-zero prediction." Font 10px muted. Position below chart.

---

### P5-V3 — CLV by segment (left, lower)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=150, Y=504, W=558, H=268

**FIELD DROPS:**
- Y-axis: `CustomerHealth[rfm_segment]`
- X-axis: `[Avg CLV Predicted]`
- Tooltips: `[Median CLV]` (per segment), `[Total Customers]`

**FORMAT SETTINGS:**
- Sort: descending by `[Avg CLV Predicted]`
- Data colours: segment palette
- Data labels: on, inside end, white, 11px, format `"R$"0.00`
- Title: "Avg predicted CLV by segment (6m)", 13px bold left.

---

### P5-V4 — CI band chart (right, top)

**Visual type:** Line chart (3 series)  
**Position:** X=720, Y=152, W=530, H=278  
**Source:** `CLVBands`

**FIELD DROPS:**
- X-axis: `CLVBands[clv_percentile_rank]`
- Y-axis (3 measures — add all three as Values):
  - `AVERAGE(CLVBands[clv_ci_lower])` — rename to "CI Lower"
  - `AVERAGE(CLVBands[clv_predicted_6m])` — rename to "Point estimate"
  - `AVERAGE(CLVBands[clv_ci_upper])` — rename to "CI Upper"

**FORMAT SETTINGS:**
- "CI Lower" line: light coral dashed, 1px
- "Point estimate" line: teal solid, 2px. Markers: on, 4px circles.
- "CI Upper" line: light coral dashed, 1px
- X-axis: title "CLV percentile rank (customers sorted by predicted CLV)". Font 11px.
- Y-axis: title "Predicted CLV (R$)". Format `"R$"0.00`.
- Legend: on, bottom. Labels: "CI Lower", "Point estimate", "CI Upper".
- Title: "CLV prediction with confidence interval", 13px bold left.
- Text box below: "Solid = point estimate · Dashed = confidence interval · Band widens at the high end (more uncertainty for high-CLV predictions)". Font 10px muted.

---

### P5-V5 — CLV vs churn quadrant scatter (right, middle)

**Visual type:** Scatter chart  
**Position:** X=720, Y=442, W=530, H=200  
**Source:** `CLVQuadrant` (aggregated — 4 rows, one per quadrant)

**FIELD DROPS:**
- X-axis: `CLVQuadrant[avg_churn]`
- Y-axis: `CLVQuadrant[avg_clv]`
- Size: `CLVQuadrant[customer_count]`
- Legend: `CLVQuadrant[quadrant]`
- Tooltips: `customer_count`, `avg_churn`, `avg_clv`, `total_gmv`

**FORMAT SETTINGS:**
- Colours: Retain=coral, Grow=teal, Reactivate=amber, Monitor=gray
- Marker size: min 15, max 40 (4 large bubbles, size = customer count)
- X-axis: title "Avg churn probability". Range 0–1. Reference line at 0.6 (coral dashed). Label "Churn threshold".
- Y-axis: title "Avg predicted CLV (R$)". Reference line at 0.93 (amber dashed). Label "Median CLV".
- Data labels: on. Content: Category. Font 12px Semibold.
- Quadrant labels: 4 text boxes in the 4 corners of the chart area. "Retain" (top-right, coral), "Grow" (top-left, teal), "Reactivate" (bottom-right, amber), "Monitor" (bottom-left, gray). Font 10px. These are static — not data-driven.
- Title: "CLV vs churn risk — action quadrants", 13px bold left.

---

### P5-V6 — Feature importance (right, bottom)

**Visual type:** Clustered bar chart (horizontal), static  
**Position:** X=720, Y=654, W=530, H=118

**Source:** Enter manually as a Power BI table (Modeling → Enter Data):

| Feature | Importance |
|---------|------------|
| Days since last order | 0.276 |
| Avg order value | 0.232 |
| Order frequency / month | 0.226 |
| Total orders | 0.085 |
| Tenure months | 0.081 |
| Avg delivery delta | 0.054 |
| % late deliveries | 0.028 |
| Categories purchased | 0.018 |

Name: `CLVFeatureImportance`.

**FIELD DROPS:**
- Y: `CLVFeatureImportance[Feature]`
- X: `CLVFeatureImportance[Importance]`

**FORMAT SETTINGS:**
- Sort: descending. Colour: single blue. Data labels: on, format `0.0%`.
- X-axis: off. Y-axis: 10px.
- Title: "XGBoost feature importance — CLV regression model", 11px bold.
- Disable all cross-filter interactions from this visual (Format → Edit interactions → set all to None). It is static information.

---

### P5 — "Actual vs Predicted" bookmark

**Create bookmark `P5_ActualVsPredicted`:**

Add a scatter chart at X=720, Y=152, W=530, H=480. Initially hidden.  
Source: `CLVResiduals`

**FIELD DROPS:**
- X-axis: `CLVResiduals[actual_gmv_post_cutoff]`
- Y-axis: `CLVResiduals[clv_predicted_6m]`
- Size: constant
- Legend: `CLVResiduals[rfm_segment]`
- Details: `CLVResiduals[customer_unique_id]` (limit with Top N filter: Top 3000 by `actual_gmv_post_cutoff`)
- Tooltips: `actual_gmv_post_cutoff`, `clv_predicted_6m`, `residual`, `customer_state`

**FORMAT SETTINGS:**
- Segment colours. Marker size 4px.
- X-axis: title "Actual 6m GMV (post-cutoff)". Format `"R$"0.00`.
- Y-axis: title "Predicted CLV (6m)". Format `"R$"0.00`.
- Perfect-prediction reference line: create a table `PerfectLine = DATATABLE("x", CURRENCY, "y", CURRENCY, {{0.0, 0.0}, {950.0, 950.0}})`. Plot as a line series overlaid. Colour red dashed. Label "Perfect prediction".
- Title: "Actual vs Predicted CLV — residual plot", 13px bold left.
- Text box below: "Points below the diagonal: model over-predicted. Points above: model under-predicted. Most points cluster near the origin (99.2% of customers had actual_gmv = 0)." Font 10px muted.

**Bookmark `P5_Default`:** normal P5 view. Residual scatter hidden.  
**Bookmark `P5_ActualVsPredicted`:** residual scatter visible. P5-V4 and P5-V5 hidden.

Buttons: "Actual vs Predicted ↗" at X=720, Y=750 → P5_ActualVsPredicted. "← Model overview" (inside panel area) → P5_Default.

---

## P6 — GEO INTELLIGENCE

**User:** Territory manager, logistics.  
**Narrative card text:** "State-level performance across GMV, churn, delivery reliability, and satisfaction. Toggle the map metric with the buttons below the map."

---

### P6-V1 — Brazil filled map (left, primary)

**Visual type:** Filled Map (Choropleth)  
**Position:** X=150, Y=28, W=628, H=724

**PRE-CONDITION:** Set the data category on `GeoPerformance[customer_state]`:
1. Model view → select `GeoPerformance` table → select `customer_state` column
2. Column tools → Data category → State or Province

Add a calculated column to `GeoPerformance`:
```dax
State Full = GeoPerformance[customer_state] & ", Brazil"
```
Set data category on `State Full` → State or Province. This helps Bing Maps locate Brazilian states correctly.

**FIELD DROPS:**
- Location: `GeoPerformance[State Full]`
- Color saturation: start with `GeoPerformance[total_gmv]`
- Tooltips: all relevant columns — `customer_count`, `churn_rate_pct`, `avg_pct_late`, `avg_review_score`, `pct_of_total_gmv`, `total_gmv`

**FORMAT SETTINGS:**
- Map styles: Light (both themes — the map itself uses Bing's own styling)
- Data colours: diverging, low=`#9FE1CB` (light teal), high=`#085041` (dark teal). Adjust for each bookmark.
- Tooltip: all fields listed above. Font 11px.
- Map controls: zoom off, pan off.
- Title: off. (The metric name is shown via a text box that changes with bookmarks — see below.)

**Active metric label:** Text box at X=150, Y=28, W=200, H=22. Changes per bookmark — e.g., "Map: Total GMV". Font 11px bold, teal. Background: none.

**INTERACTION:** State click → cross-filters P6-V2 (table) and P6-V3, P6-V4. Right-click → Drill through → P3 (Churn Risk) filtered to that state (P3 must have `customer_state` in its drill-through well — add `ChurnSignals[customer_state]` to P3's drill-through fields).

---

### P6 — Map metric toggle (4 bookmarks)

For each bookmark: change the Color saturation field on the map and the metric label text box.

**P6_GMV (default):** Color saturation = `total_gmv`. Scale: teal. Label: "Map: Total GMV (R$)".
**P6_Churn:** Color saturation = `churn_rate_pct`. Scale: white → coral (high = bad). Label: "Map: Churn rate (%)".
**P6_Late:** Color saturation = `avg_pct_late`. Scale: white → amber (high = bad). Label: "Map: % late deliveries".
**P6_Review:** Color saturation = `avg_review_score`. Scale: coral (low) → teal (high = good, inverted). Label: "Map: Avg review score".

**Implementation:** In Power BI, you cannot change the Color saturation field via bookmarks directly — bookmarks save the visual's filter state, not its field assignments. Workaround: build 4 separate filled map visuals (one per metric), position them on top of each other, and toggle their visibility via bookmarks. Each map has its own saturation field and colour scale. This is the correct production approach.

4 toggle buttons at X=150, Y=754, each W=148, H=28, gap 4px:
- "GMV" → P6_GMV. "Churn rate" → P6_Churn. "Late deliveries" → P6_Late. "Review score" → P6_Review.
- Style active button: border teal 1.5px, font bold. Inactive: muted border.

---

### P6-V2 — State ranking table (right, top)

**Visual type:** Table  
**Position:** X=790, Y=28, W=460, H=382  
**Source:** `GeoPerformance`

**FIELD DROPS (columns):**

| # | Column | Title | Width | Format |
|---|--------|-------|-------|--------|
| 1 | `customer_state` | State | 48px | text |
| 2 | `total_gmv` | GMV (R$) | 102px | `"R$"#,0` |
| 3 | `customer_count` | Customers | 82px | `#,0` |
| 4 | `churn_rate_pct` | Churn % | 72px | `0.0%` |
| 5 | `avg_pct_late` | Late % | 62px | `0.0%` |
| 6 | `avg_review_score` | Review | 70px | `0.00` |

**FORMAT SETTINGS:**
- Conditional bg on `churn_rate_pct`: > 0.73 → coral@15%
- Conditional bg on `avg_pct_late`: > 0.10 → amber@15%
- Sort: `total_gmv` descending (default). Columns are sortable — click header.
- Header: dark style.
- Row height: 28px. Scrollable (27 rows total).
- Title: "State performance ranking", 13px bold.

**INTERACTION:** Row click → cross-filters P6-V1 (map), P6-V3, P6-V4.

---

### P6-V3 — GMV concentration bar (right, middle-left)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=790, Y=422, W=222, H=180

**FIELD DROPS:**
- Y: `GeoPerformance[dashboard_state_label]` (collapses <2% states to "Other")
- X: `GeoPerformance[total_gmv]`

**FORMAT SETTINGS:**
- Sort: descending. Colour: single teal.
- Data labels: inside end, white, 11px, format `"R$"#,0,,,"M"`.
- Title: "GMV by state (collapsed)", 11px bold.
- Text box below: "Top 5 states = 73.9% of total GMV. SP alone = 37.4%." Font 10px muted.

---

### P6-V4 — Delivery vs satisfaction scatter (right, middle-right)

**Visual type:** Scatter chart  
**Position:** X=1020, Y=422, W=230, H=180  
**Source:** `GeoPerformance`

**FIELD DROPS:**
- X-axis: `GeoPerformance[avg_pct_late]`
- Y-axis: `GeoPerformance[avg_review_score]`
- Size: `GeoPerformance[customer_count]`
- Details: `GeoPerformance[customer_state]`
- Tooltips: all columns

**FORMAT SETTINGS:**
- Single colour: blue. Size min 4, max 18.
- X-axis: title "% late deliveries". Format `0%`. Gridlines off.
- Y-axis: title "Avg review score". Range 3.5–4.5.
- Analytics: X constant line at 0.10 (coral dashed). Y constant line at 4.0 (amber dashed).
- Data labels: category (state code). Font 9px.
- Title: "Late delivery vs satisfaction", 11px bold.

---

### P6-V5 — Best/worst delivery callout (right, bottom)

**Visual type:** 2× Text boxes (static)  
**Position:** X=790, Y=614, W=460, H=138

Left box (best): X=790, Y=614, W=222, H=138.
- Background: teal@10%. Left border: teal 3px (simulate with a 3px-wide teal rectangle).
- Header: "Best delivery" 11px bold teal.
- Body: "RO · AM · PA · MT · PE" 12px. "Avg delta: −15 to −20 days early" 10px muted.

Right box (worst): X=1020, Y=614, W=230, H=138.
- Background: coral@10%. Left border: coral 3px.
- Header: "Worst delivery" 11px bold coral.
- Body: "CE · BA · ES · MA · AL" 12px. "Avg delta: −5 to +2 days, high late %" 10px muted.

---

## P7 — SENTIMENT & NLP

**User:** CX analyst, product quality.  
**Narrative card text:** "Review sentiment scored using LeIA (a Portuguese-language VADER fork). Only 41.4% of reviews contain text — sentiment is absent for the rest. Sarcasm flags show 1-star reviews with positive-scoring text."

---

### P7-V1 — NLP KPI strip (5 cards, top)

**Position:** Y=28, each W=210, H=90, gap=6. X=150, 366, 582, 798, 1014.

| # | Measure | Format | Label | Bg |
|---|---------|--------|-------|-----|
| 1 | `[Reviews Scored]` | `#,0` | Reviews scored | Default |
| 2 | `[Pct Positive Sentiment]` | `0.0%` | % positive | Teal@10% |
| 3 | `[Pct Neutral Sentiment]` | `0.0%` | % neutral | Default |
| 4 | `[Pct Negative Sentiment]` | `0.0%` | % negative | Coral@10% |
| 5 | `[Sarcasm Flag Count]` | `#,0` | Likely mis-rated (1★ + positive text) | Amber@10% |

---

### P7-V2 — Compound score histogram (left, top)

**Visual type:** Clustered column chart  
**Position:** X=150, Y=130, W=540, H=282  
**Source:** `SentimentHistogram`

**FIELD DROPS:**
- X-axis: `SentimentHistogram[compound_bucket]` — sort by `SentimentHistogram[bucket_order]`
- Y-axis: `SentimentHistogram[review_count]`

**FORMAT SETTINGS:**
- Bar colours via conditional formatting rules on `bucket_order`:
  - 1, 2, 3 → coral (negative zone)
  - 4 → gray (neutral zone)
  - 5, 6, 7 → teal (positive zone)
- Analytics → Constant line at 3.5 (between buckets 3 and 4 — the -0.05 boundary). Colour coral dashed. Label "-0.05 threshold".
- Analytics → Constant line at 4.5 (between buckets 4 and 5 — the +0.05 boundary). Colour teal dashed. Label "+0.05 threshold".
- X-axis: 10px, rotate 30°. Title off.
- Y-axis: format `#,0`. Title "Reviews". Gridlines 0.5px.
- Data labels: on, top, 10px.
- Title: "Compound sentiment distribution (LeIA ±0.05 neutral zone)", 13px bold left.

---

### P7-V3 — Sentiment by star rating (left, bottom)

**Visual type:** 100% Stacked bar chart  
**Position:** X=150, Y=424, W=540, H=248  
**Source:** `SentimentScores`

**FIELD DROPS:**
- Y-axis: `SentimentScores[review_score]` — sort ascending (1→5)
- Values: `[Reviews Scored]` count (or add a simple count measure: `Review Count = COUNTROWS(SentimentScores)`)
- Legend: `SentimentScores[sentiment_label]`

**FORMAT SETTINGS:**
- Legend colours: positive=teal, neutral=gray, negative=coral
- 100% stacked mode — shows composition per star rating
- Data labels: on, percentage format, font 10px white
- X-axis: format `0%`. Title off.
- Y-axis: title "Review score (1–5)". Labels 11px.
- Title: "Sentiment composition by star rating", 13px bold left.
- Annotation text box below: "ⓘ Positive-sentiment 1★ reviews may indicate sarcasm or mis-rating — see the 'Likely mis-rated' count in the KPI strip above." Font 10px muted.

---

### P7-V4 — Monthly sentiment trend (right, top)

**Visual type:** Line and clustered column chart (combo)  
**Position:** X=702, Y=130, W=548, H=262  
**Source:** `SentimentTrend`

**FIELD DROPS:**
- Shared axis: `SentimentTrend[review_month]`
- Column values: `SentimentTrend[review_count]` — secondary Y axis
- Line values: `SentimentTrend[avg_compound]` — primary Y axis

**FORMAT SETTINGS:**
- Line: teal, 2px solid. Markers: on, 6px circles.
- Columns: light gray `#D3D1C7`, 40% transparency.
- Primary Y: title "Avg compound score". Range -0.3 to 0.5.
- Secondary Y: title "Review count". Format `#,0`. Gridlines off.
- Analytics on primary Y: Constant line at 0 (gray dashed). Label "Neutral baseline".
- X-axis: Month/Year, 11px. Title off.
- Title: "Monthly sentiment trend vs review volume", 13px bold left.

---

### P7-V5 — Avg sentiment by action type (right, middle)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=702, Y=404, W=548, H=142  
**Source:** `CustomerHealth`

**FIELD DROPS:**
- Y: `CustomerHealth[latest_action_type]`
- X: `[Avg Sentiment]` measure

**FORMAT SETTINGS:**
- Colours: RETENTION_CAMPAIGN=coral, REACTIVATION=amber, MONITOR=gray (manual override)
- Sort: descending
- Data labels: on. Format `0.00`.
- Analytics: Constant line at 0 (gray dashed). Label "Neutral".
- Title: "Avg sentiment score by action type", 13px bold left.
- Context: RETENTION has the lowest avg sentiment (0.16), MONITOR the highest (0.22). Add a text box annotation: "Lower sentiment for flagged customers validates using sentiment as a churn predictor feature." Font 10px muted.

---

### P7-V6 — Sentiment by segment (right, lower)

**Visual type:** Clustered bar chart (horizontal)  
**Position:** X=702, Y=558, W=548, H=214  
**Source:** `CustomerHealth`

**FIELD DROPS:**
- Y: `CustomerHealth[rfm_segment]`
- X: `[Avg Sentiment]` measure

**FORMAT SETTINGS:**
- Segment colours
- Sort: descending
- Data labels: format `0.00`
- Constant line at 0 (neutral). Gray dashed.
- Title: "Avg sentiment by RFM segment", 13px bold left.
- Expected: Champions highest (~0.30+), At Risk and Hibernating lowest (~0.10–0.15). If Champions doesn't appear highest, verify `CustomerHealth[avg_sentiment_score]` is populated — it is NULL for customers without any reviews, which can skew segment averages.

---

### P7 — Sarcasm review table (hidden, toggled by bookmark)

**Build this hidden panel at:** X=702, Y=404, W=548, H=366. Initially hidden.

**Visual type:** Table  
**Source:** `SentimentScores`

**FIELD DROPS:**
1. `SentimentScores[review_score]` — title "Stars" — W=50px
2. `SentimentScores[compound_score]` — title "Sentiment" — W=80px — conditional bg: > 0.3 → amber@20%
3. `SentimentScores[review_comment_message]` — title "Review text" — W=400px

**Visual-level filters (all required):**
- `SentimentScores[review_score]` = 1
- `SentimentScores[compound_score]` > 0.3
- `SentimentScores[compound_score]` is not blank

**FORMAT SETTINGS:**
- Sort: `compound_score` descending
- Row height: 48px (text wraps in the review text column)
- Header: dark style

**Text box above table:** "Reviews with 1★ rating but positive-scoring sentiment text — likely sarcasm, translation artifacts, or accidental mis-rating. Compound score > 0.3 = LeIA scored these as positive despite the 1-star." Font 11px, amber.

**Bookmarks:**
- `P7_Default`: table hidden. P7-V5 and P7-V6 visible.
- `P7_Sarcasm`: table visible. P7-V5 and P7-V6 hidden.

**Buttons at X=702, Y=782:**
- "Likely mis-rated reviews ↗" W=270, H=28 → P7_Sarcasm
- "← Sentiment overview" W=270, H=28 (same position, toggle) → P7_Default

---

## PART 5 — BOOKMARKS INVENTORY

Build in this order. Use View → Selection pane to control visibility per bookmark.

| Bookmark | Page | Effect | Trigger button label |
|----------|------|--------|---------------------|
| `P1_Top5` | P1 | Default. Top-5 bar, no Pareto line | "Top 5 States" |
| `P1_Pareto` | P1 | All states + cumulative GMV line | "Pareto view" |
| `P3_Operational` | P3 | Normal churn view. WhatIf panel hidden | "← Operational view" (inside panel) |
| `P3_WhatIf` | P3 | WhatIf panel visible. Charts hidden | "What-If simulator ↗" |
| `P4_RFM` | P4 | Scatter legend=rfm_segment | "RFM segments" |
| `P4_KMeans` | P4 | Scatter legend=km_cluster | "K-means clusters" |
| `P5_Default` | P5 | Normal CLV view | "← Model overview" (inside panel) |
| `P5_ActualVsPredicted` | P5 | Residual scatter. Normal panel hidden | "Actual vs Predicted ↗" |
| `P6_GMV` | P6 | Map 1 visible (GMV colour) | "GMV" |
| `P6_Churn` | P6 | Map 2 visible (Churn colour) | "Churn rate" |
| `P6_Late` | P6 | Map 3 visible (Late delivery colour) | "Late deliveries" |
| `P6_Review` | P6 | Map 4 visible (Review score colour) | "Review score" |
| `P7_Default` | P7 | Sarcasm table hidden. V5 and V6 visible | "← Sentiment overview" (inside panel) |
| `P7_Sarcasm` | P7 | Sarcasm table visible. V5 and V6 hidden | "Likely mis-rated reviews ↗" |

**Total: 14 bookmarks.**

When creating bookmarks: in the Bookmarks pane → right-click each bookmark → uncheck "Data" (keep Data unchecked) and keep "Display" checked. This means the bookmark controls which visuals are visible/hidden but does not reset slicer state — critical so that user's active filter selections are preserved when they toggle bookmark views.

Exception: `P6_GMV/Churn/Late/Review` — these control which map visual is visible. Set them with both Data and Display checked so the correct map's filter state is also captured.

---

## PART 6 — DRILL-THROUGH CONFIGURATION

| From | Field | To | Effect |
|------|-------|----|--------|
| Any page (row click) | `customer_unique_id` | P2 Customer 360 | Filters P2 to 1 customer |
| P1 state bar | `customer_state` | P6 Geo Intelligence | Filters map to that state |
| P6 map / table | `customer_state` | P3 Churn Risk | Filters churn table to that state |
| P4 scatter | `rfm_segment` | P3 Churn Risk | Filters churn table to that segment |

**Setup steps for each:**
1. Navigate to the destination page (e.g., P2).
2. Visualizations pane → scroll to "Drill through" section at the bottom.
3. Drag the trigger field (e.g., `CustomerHealth[customer_unique_id]`) into "Add drill-through fields here".
4. Power BI auto-adds a back button — format it to match your Back Button style (see P2 sidebar).
5. Return to the source page. Right-click a data point → Drill through → the destination page name should appear in the submenu.

**Testing:** Right-click a row in P3's customer table → Drill through → Customer 360. Verify all P2 visuals show only that one customer's data.

---

## PART 7 — VALIDATION CHECKLIST

Run the full checklist before considering the report production-ready.

**Pre-build validation:**
- [ ] DAX script EVALUATE result shows `Status = "PASS — all checks cleared"`
- [ ] `Total GMV` ≈ R$15,843,553
- [ ] `Total Customers` = 96,096
- [ ] `HIGH Priority Count` = 11,957
- [ ] `Reviews Scored` = 40,641
- [ ] `_Row Count ActionQueue` = 96,096
- [ ] `_Null CLV Count` ≈ 24,910

**Navigation rail (Part 3.3, after Pass B):**
- [ ] All 6 buttons present and identically positioned on all 7 pages
- [ ] Each button's Format → Action is set to a Bookmark (not blank, not "Page navigation")
- [ ] Clicking each button from every other page lands on the correct page
- [ ] Exactly one active-state indicator is visible at a time, and it matches the current page
- [ ] Icon glyphs render correctly after a PDF export test (no missing-glyph boxes)
- [ ] Data-as-of strip is visible and shows the correct text on all 7 pages
- [ ] Narrative card text matches the page it's on (not copy-pasted from a different page)
- [ ] A slicer selection made before clicking a nav button is still applied after navigating (confirms bookmarks have "Data" unchecked per Part 5)

**P1 Command Centre:**
- [ ] GMV card: ≈ R$15.8M
- [ ] Customers card: 96,096
- [ ] Churn rate: ≈ 71.0%
- [ ] HIGH priority: 11,957
- [ ] Donut: MONITOR 59.9% / REACTIVATION 27.7% / RETENTION_CAMPAIGN 12.4%
- [ ] Area chart has reference line at May 2018
- [ ] State bar: SP is largest
- [ ] Pareto bookmark toggles correctly — all states visible
- [ ] Anomaly outliers table is non-empty

**P2 Customer 360:**
- [ ] Drill-through from P3 row populates all visuals correctly
- [ ] Churn probability card shows a value between 0 and 1
- [ ] Trigger reason text box is not blank
- [ ] Order timeline dots appear (may be empty if customer has 0 reviews)
- [ ] Back button returns to P3 (or whichever page was drilled from)

**P3 Churn & Action Risk:**
- [ ] Table shows rows from `ChurnSignals` (not 96,096 — this view filters to is_churned=1 or churn_probability>0.4)
- [ ] Urgency column sorts descending correctly
- [ ] Histogram: spike near 0.0–0.1 (structural one-time buyers with low model confidence) and spike at 0.9–1.0 (fully churned customers)
- [ ] What-If slider responds: move to 0.5, actionable count increases; move to 0.8, decreases
- [ ] `P3_WhatIf` bookmark toggles correctly — panel appears, charts hide

**P4 Segmentation & RFM:**
- [ ] Scatter loads in < 10 seconds (if slower, `customer_unique_id` is in Details — remove it)
- [ ] Scatter shows 9 legend colours
- [ ] Heatmap shows all 9 × 3 = 27 cells
- [ ] Treemap: Frequent Low-Spender is largest area, Champions has largest GMV per customer
- [ ] K-means bookmark: scatter colour changes to 5 numeric clusters
- [ ] Tooltip page appears on scatter hover

**P5 CLV & Predicted Value:**
- [ ] Histogram: "No CLV" is the largest bar (≈ 24,910)
- [ ] CLV disclaimer strip visible
- [ ] CI band chart shows 3 lines (point estimate + 2 CI bounds)
- [ ] Quadrant scatter shows 4 coloured bubbles
- [ ] Feature importance bars sum to approximately 100%
- [ ] `P5_ActualVsPredicted` bookmark toggles correctly

**P6 Geo Intelligence:**
- [ ] Brazil map renders with coloured state fills (if map shows empty, check data category on `State Full`)
- [ ] All 4 map metric bookmarks change the colour scale
- [ ] SP is darkest in GMV mode
- [ ] CE/BA/AL are darkest/most amber in late delivery mode
- [ ] State table scrolls through 27 rows
- [ ] Scatter: negative correlation visible (higher late % → lower review score)

**P7 Sentiment & NLP:**
- [ ] KPI strip: positive ≈ 51.9%, neutral ≈ 33.0%, negative ≈ 15.1%
- [ ] Sarcasm count is non-zero
- [ ] Histogram shows 7 bars with coral on left, teal on right
- [ ] Monthly trend spans Sept 2016 to Oct 2018
- [ ] Stacked bar: 1-star row is predominantly coral (negative), 5-star is predominantly teal
- [ ] Segment bar: Champions is highest, At Risk/Hibernating lowest
- [ ] `P7_Sarcasm` bookmark: table appears with filtered reviews

---

## PART 8 — PUBLISHING NOTES

**Screenshot all 7 pages at 1280×800:**
Save in `powerbi/screenshots/` with filenames:
- `P1_command_centre.png`
- `P2_customer_360.png`
- `P3_churn_action_risk.png`
- `P4_segmentation_rfm.png`
- `P5_clv_predicted_value.png`
- `P6_geo_intelligence.png`
- `P7_sentiment_nlp.png`

**PDF export:**
File → Export → Export to PDF. Save as `powerbi/CRM_Customer_Intelligence.pdf`. The PDF lets any reviewer see all dashboards without Power BI Desktop.

**Git — never commit the .pbix:**
Power BI `.pbix` files embed the SQL Server connection string and may embed query results. Add to `.gitignore`:
```
powerbi/*.pbix
```
Commit: PDF, screenshots, theme JSON files, and the DAX script. Store the `.pbix` in OneDrive or a Power BI Service workspace.

**Files to commit to `powerbi/` folder:**
```
powerbi/
├── CRM_Customer_Intelligence.pdf
├── CRM_Intelligence_Dark.json
├── CRM_Intelligence_Light.json
├── screenshots/
│   ├── P1_command_centre.png
│   ├── P2_customer_360.png
│   ├── P3_churn_action_risk.png
│   ├── P4_segmentation_rfm.png
│   ├── P5_clv_predicted_value.png
│   ├── P6_geo_intelligence.png
│   └── P7_sentiment_nlp.png
```

---

*End of implementation guide v2.0. Build order: P1 → P6 → P3 → P4 → P5 → P7 → P2.*