```DAX
-- =================================================================================
-- MODULE:     _CRM Measures + Calculated Columns
-- PROJECT:    CRM Customer Intelligence Module — Olist E-Commerce
-- AUTHOR:     Abdallah A Khames
-- VERSION:    1.0
-- CREATED:    2026-06
-- DATABASE:   CRM_Analytics (SQL Server)
-- DEPENDS ON: CustomerHealth (mart.vw_customer_health)
--             ChurnSignals   (mart.vw_churn_signals)
--             GeoPerformance (mart.vw_geo_performance)
--             SentimentScores (mart.sentiment_scores)
--             ActionQueue     (mart.crm_action_queue)
--             RefreshLog      (mart.refresh_log)
--             _Measures       (empty placeholder table — Enter Data with one blank col)
-- =================================================================================
--
-- DESCRIPTION:
--   All DAX measures and calculated columns for the CRM Intelligence Power BI report.
--   Covers: GMV, churn, CLV, sentiment, segmentation, geo, action queue, What-If.
--   Calculated columns are defined as CALCULATE COLUMN measures — apply them
--   manually in the Model view after running this script (DAX Query View cannot
--   create calculated columns; see SECTION 10 instructions).
--
-- EXECUTION:
--   1. Paste this entire script into DAX Query View
--   2. Click "Run" (or Shift+Enter)
--   3. Click "Update model with changes" when prompted
--   4. Verify the EVALUATE block returns Status = "PASS"
--
-- NOTE ON CALCULATED COLUMNS (SECTION 10):
--   Power BI DAX Query View cannot create calculated columns via DEFINE.
--   For each column in Section 10: go to the 'mart vw_customer_health' table in Model view,
--   click "New column", paste the formula body only (without the MEASURE wrapper).
--
-- =================================================================================

DEFINE

-- ================================================================================
-- SECTION 1: PORTFOLIO KPIs
-- Core business metrics. All reference CustomerHealth (vw_customer_health).
-- ================================================================================

-- | Total GMV | Sum of customer lifetime GMV across all orders (BRL) |
MEASURE '_Measures'[Total GMV] =
    SUM( 'mart vw_customer_health'[total_gmv] )

-- | Total Customers | Distinct customer count |
MEASURE '_Measures'[Total Customers] =
    DISTINCTCOUNT( 'mart vw_customer_health'[customer_unique_id] )

-- | Avg Order Value | Portfolio-level average order value |
MEASURE '_Measures'[Avg Order Value] =
    AVERAGE( 'mart vw_customer_health'[avg_order_value] )

-- | Total Freight Paid | Sum of freight across all customers |
MEASURE '_Measures'[Total Freight Paid] =
    SUM( 'mart vw_customer_health'[total_freight_paid] )

-- | Freight Ratio | Freight as % of GMV — cost efficiency lens |
MEASURE '_Measures'[Freight Ratio] =
    DIVIDE( [Total Freight Paid], [Total GMV], 0 )

-- | As Of Date | Latest data refresh date from refresh_log |
MEASURE '_Measures'[As Of Date] =
    FIRSTNONBLANK( 'mart refresh_log'[as_of_date], 1 )

-- | As Of Date Label | Formatted date string for banner display |
MEASURE '_Measures'[As Of Date Label] =
    "Data as of "
    & FORMAT( [As Of Date], "MMM DD, YYYY" )
    & "  ·  ML cutoff 2018-05-01  ·  Churn window 180 days"

-- ================================================================================
-- SECTION 2: CHURN METRICS
-- Rule-based (is_churned) and model-based (churn_probability) measures.
-- ================================================================================

-- | Churned Customers | Count of customers where is_churned = 1 (180d rule) |
MEASURE '_Measures'[Churned Customers] =
    COUNTROWS( FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[is_churned] = 1 ) )

-- | Churn Rate | Rule-based churn rate (180-day window) |
MEASURE '_Measures'[Churn Rate] =
    DIVIDE( [Churned Customers], [Total Customers], 0 )

-- | Avg Churn Probability | Model-predicted mean churn probability |
MEASURE '_Measures'[Avg Churn Probability] =
    AVERAGE( 'mart vw_customer_health'[churn_probability] )

-- | High Churn Customers | Customers with churn_probability >= 0.6 |
MEASURE '_Measures'[High Churn Customers] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[churn_probability] >= 0.6 )
    )

-- | High Churn Rate | % of customers above the deployed 0.6 threshold |
MEASURE '_Measures'[High Churn Rate] =
    DIVIDE( [High Churn Customers], [Total Customers], 0 )

-- | Avg Urgency Score | Mean urgency from vw_churn_signals (0–100 triage) |
MEASURE '_Measures'[Avg Urgency Score] =
    AVERAGE( 'mart vw_churn_signals'[urgency_score] )

-- | Avg Pct Late Deliveries | Portfolio-level late delivery rate |
MEASURE '_Measures'[Avg Pct Late] =
    AVERAGE( 'mart vw_customer_health'[pct_late_deliveries] )

-- | Avg Delivery Delta | Average days early (negative) or late (positive) |
MEASURE '_Measures'[Avg Delivery Delta] =
    AVERAGE( 'mart vw_customer_health'[avg_delivery_delta_days] )

-- | Avg Review Score | Portfolio-level average customer review score |
MEASURE '_Measures'[Avg Review Score] =
    AVERAGE( 'mart vw_customer_health'[avg_review_score] )

-- | Avg Health Score | Portfolio-level average customer health score (0–100) |
MEASURE '_Measures'[Avg Health Score] =
    AVERAGE( 'mart vw_customer_health'[customer_health_score] )

-- | Segment Churn Rate | Churn rate within the current filter context (used in segment table) |
MEASURE '_Measures'[Segment Churn Rate] =
    DIVIDE(
        COUNTROWS( FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[is_churned] = 1 ) ),
        [Total Customers],
        0
    )

-- ================================================================================
-- SECTION 3: ACTION QUEUE METRICS
-- Measures derived from the CRM action queue output.
-- ================================================================================

-- | HIGH Priority Count | Customers assigned RETENTION_CAMPAIGN (HIGH priority) |
MEASURE '_Measures'[HIGH Priority Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_priority] = "HIGH" )
    )

-- | MED Priority Count | Customers assigned REACTIVATION (MED priority) |
MEASURE '_Measures'[MED Priority Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_priority] = "MED" )
    )

-- | LOW Priority Count | Customers in MONITOR (LOW priority) |
MEASURE '_Measures'[LOW Priority Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_priority] = "LOW" )
    )

-- | Actionable Customers | Customers not in MONITOR — need a real action |
MEASURE '_Measures'[Actionable Customers] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_type] <> "MONITOR" )
    )

-- | Actionable Pct | % of total customers flagged for action |
MEASURE '_Measures'[Actionable Pct] =
    DIVIDE( [Actionable Customers], [Total Customers], 0 )

-- | Flagged GMV | Total GMV of customers with an active action (not MONITOR) |
MEASURE '_Measures'[Flagged GMV] =
    CALCULATE(
        SUM( 'mart vw_customer_health'[total_gmv] ),
        'mart vw_customer_health'[latest_action_type] <> "MONITOR"
    )

-- | Retention Campaign GMV | GMV at stake for HIGH-priority retention customers |
MEASURE '_Measures'[Retention GMV] =
    CALCULATE(
        SUM( 'mart vw_customer_health'[total_gmv] ),
        'mart vw_customer_health'[latest_action_type] = "RETENTION_CAMPAIGN"
    )

-- | Reactivation GMV | GMV at stake for MED-priority reactivation customers |
MEASURE '_Measures'[Reactivation GMV] =
    CALCULATE(
        SUM( 'mart vw_customer_health'[total_gmv] ),
        'mart vw_customer_health'[latest_action_type] = "REACTIVATION"
    )

-- | Retention Campaign Count | Number of RETENTION_CAMPAIGN assignments |
MEASURE '_Measures'[Retention Campaign Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_type] = "RETENTION_CAMPAIGN" )
    )

-- | Reactivation Count | Number of REACTIVATION assignments |
MEASURE '_Measures'[Reactivation Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_type] = "REACTIVATION" )
    )

-- | Monitor Count | Number of customers in MONITOR (baseline, no action) |
MEASURE '_Measures'[Monitor Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', 'mart vw_customer_health'[latest_action_type] = "MONITOR" )
    )

-- ================================================================================
-- SECTION 4: CLV METRICS
-- Customer Lifetime Value predictions from clv_model.py output.
-- ================================================================================

-- | CLV With Prediction | Count of customers with a non-null CLV prediction |
MEASURE '_Measures'[CLV With Prediction] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] ) )
    )

-- | CLV Coverage Pct | % of customers that have a CLV prediction |
MEASURE '_Measures'[CLV Coverage Pct] =
    DIVIDE( [CLV With Prediction], [Total Customers], 0 )

-- | Avg CLV Predicted | Mean predicted 6-month CLV (non-null customers only) |
MEASURE '_Measures'[Avg CLV Predicted] =
    CALCULATE(
        AVERAGE( 'mart vw_customer_health'[clv_predicted_6m] ),
        NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
    )

-- | Median CLV | 50th percentile of predicted CLV (non-null only) |
-- | Used as the high/low CLV split threshold in action rules and quadrant chart |
MEASURE '_Measures'[Median CLV] =
    CALCULATE(
        PERCENTILE.INC( 'mart vw_customer_health'[clv_predicted_6m], 0.5 ),
        NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
    )

-- | Max CLV | Highest predicted 6-month CLV in current filter context |
MEASURE '_Measures'[Max CLV] =
    MAX( 'mart vw_customer_health'[clv_predicted_6m] )

-- | P90 CLV | 90th percentile CLV — VIP threshold reference |
MEASURE '_Measures'[P90 CLV] =
    CALCULATE(
        PERCENTILE.INC( 'mart vw_customer_health'[clv_predicted_6m], 0.9 ),
        NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
    )

-- | Total CLV Portfolio | Sum of all predicted 6m CLV values |
MEASURE '_Measures'[Total CLV Portfolio] =
    CALCULATE(
        SUM( 'mart vw_customer_health'[clv_predicted_6m] ),
        NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
    )

-- | Avg CLV per Segment | Used in segment-level CLV bar chart |
MEASURE '_Measures'[Avg GMV per Customer] =
    DIVIDE( [Total GMV], [Total Customers], 0 )

-- ================================================================================
-- SECTION 5: SENTIMENT METRICS
-- LeIA compound scores from sentiment.py output.
-- ================================================================================

-- | Reviews Scored | Count of reviews with a non-null compound_score |
MEASURE '_Measures'[Reviews Scored] =
    COUNTROWS(
        FILTER( 'mart sentiment_scores', NOT ISBLANK( 'mart sentiment_scores'[compound_score] ) )
    )

-- | Reviews With Text | Reviews that had non-null review_comment_message |
MEASURE '_Measures'[Reviews With Text] =
    COUNTROWS(
        FILTER( 'mart sentiment_scores', NOT ISBLANK( 'mart sentiment_scores'[review_comment_message] ) )
    )

-- | Pct Positive Sentiment | % of scored reviews labelled "positive" |
MEASURE '_Measures'[Pct Positive Sentiment] =
    DIVIDE(
        COUNTROWS( FILTER( 'mart sentiment_scores', 'mart sentiment_scores'[sentiment_label] = "positive" ) ),
        [Reviews Scored],
        0
    )

-- | Pct Neutral Sentiment | % labelled "neutral" |
MEASURE '_Measures'[Pct Neutral Sentiment] =
    DIVIDE(
        COUNTROWS( FILTER( 'mart sentiment_scores', 'mart sentiment_scores'[sentiment_label] = "neutral" ) ),
        [Reviews Scored],
        0
    )

-- | Pct Negative Sentiment | % labelled "negative" |
MEASURE '_Measures'[Pct Negative Sentiment] =
    DIVIDE(
        COUNTROWS( FILTER( 'mart sentiment_scores', 'mart sentiment_scores'[sentiment_label] = "negative" ) ),
        [Reviews Scored],
        0
    )

-- | Avg Sentiment | Mean compound score (non-null reviews only) |
MEASURE '_Measures'[Avg Sentiment] =
    CALCULATE(
        AVERAGE( 'mart vw_customer_health'[avg_sentiment_score] ),
        NOT ISBLANK( 'mart vw_customer_health'[avg_sentiment_score] )
    )

-- | Avg Compound Score | Review-level mean compound score (from SentimentScores) |
MEASURE '_Measures'[Avg Compound Score] =
    CALCULATE(
        AVERAGE( 'mart sentiment_scores'[compound_score] ),
        NOT ISBLANK( 'mart sentiment_scores'[compound_score] )
    )

-- | Sarcasm Flag Count | Reviews with 1-star rating but positive compound score > 0.3 |
-- | Signals possible sarcasm, translation artifact, or accidental mis-rating |
MEASURE '_Measures'[Sarcasm Flag Count] =
    COUNTROWS(
        FILTER(
            'mart sentiment_scores',
            'mart sentiment_scores'[review_score] = 1
                && 'mart sentiment_scores'[compound_score] > 0.3
                && NOT ISBLANK( 'mart sentiment_scores'[compound_score] )
        )
    )

-- ================================================================================
-- SECTION 6: GEO METRICS
-- State-level aggregates from vw_geo_performance.
-- ================================================================================

-- | National GMV | Total GMV across all states — denominator for concentration |
MEASURE '_Measures'[National GMV] =
    CALCULATE( SUM( 'mart vw_geo_performance'[total_gmv] ), ALL( 'mart vw_geo_performance' ) )

-- | State GMV Share | State's GMV as % of national total |
MEASURE '_Measures'[State GMV Share] =
    DIVIDE( SUM( 'mart vw_geo_performance'[total_gmv] ), [National GMV], 0 )

-- | Avg State Churn Rate | Mean churn rate across states in filter context |
MEASURE '_Measures'[Avg State Churn Rate] =
    AVERAGE( 'mart vw_geo_performance'[churn_rate_pct] )

-- | Avg State Late Pct | Mean late delivery % across states |
MEASURE '_Measures'[Avg State Late Pct] =
    AVERAGE( 'mart vw_geo_performance'[pct_late_deliveries] )

-- | Avg State Review Score | Mean review score across states |
MEASURE '_Measures'[Avg State Review Score] =
    AVERAGE( 'mart vw_geo_performance'[avg_review_score] )

-- ================================================================================
-- SECTION 7: WHAT-IF ACTION SIMULATOR
-- These measures respond to the Threshold Parameter slicer on P3.
-- Create the parameter first: Modeling → New parameter → Numeric range
--   Name: "Threshold Parameter", Min: 0.2, Max: 0.9, Increment: 0.05, Default: 0.6
-- Power BI auto-generates a measure called [Threshold Parameter Value].
-- ================================================================================

-- | WhatIf Actionable | Customers above the slider threshold (any CLV) |
MEASURE '_Measures'[WhatIf Actionable] =
    COUNTROWS(
        FILTER(
            'mart vw_customer_health',
            'mart vw_customer_health'[churn_probability] >= 'Threshold Parameter'[Threshold Parameter Value]
        )
    )

-- | WhatIf Retention Campaign | High-churn + above-median CLV at selected threshold |
MEASURE '_Measures'[WhatIf Retention Campaign] =
    COUNTROWS(
        FILTER(
            'mart vw_customer_health',
            'mart vw_customer_health'[churn_probability] >= 'Threshold Parameter'[Threshold Parameter Value]
                && NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
                && 'mart vw_customer_health'[clv_predicted_6m] >= [Median CLV]
        )
    )

-- | WhatIf Reactivation | High-churn + below-median CLV at selected threshold |
MEASURE '_Measures'[WhatIf Reactivation] =
    COUNTROWS(
        FILTER(
            'mart vw_customer_health',
            'mart vw_customer_health'[churn_probability] >= 'Threshold Parameter'[Threshold Parameter Value]
                && (
                    ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
                    || 'mart vw_customer_health'[clv_predicted_6m] < [Median CLV]
                )
        )
    )

-- | WhatIf Delta Actionable | Change vs default threshold (0.6) |
MEASURE '_Measures'[WhatIf Delta Actionable] =
    [WhatIf Actionable] - [Actionable Customers]

-- | WhatIf Delta Label | Human-readable change direction for card display |
MEASURE '_Measures'[WhatIf Delta Label] =
    VAR Delta = [WhatIf Delta Actionable]
    RETURN
    IF(
        Delta > 0,
        "▲ " & FORMAT( Delta, "#,0" ) & " more than default (0.6)",
        IF(
            Delta < 0,
            "▼ " & FORMAT( ABS( Delta ), "#,0" ) & " fewer than default (0.6)",
            "No change from default (0.6)"
        )
    )

-- ================================================================================
-- SECTION 8: NEXT-PURCHASE METRICS
-- Weibull AFT survival model output from next_purchase.py.
-- Only 2,996 repeat customers (3.12%) have predictions.
-- ================================================================================

-- | Next Purchase With Prediction | Count with a non-null prediction |
MEASURE '_Measures'[Next Purchase With Prediction] =
    COUNTROWS(
        FILTER(
            'mart vw_customer_health',
            NOT ISBLANK( 'mart vw_customer_health'[expected_next_purchase_days] )
        )
    )

-- | Avg Next Purchase Days | Expected days to next order (repeat customers only) |
MEASURE '_Measures'[Avg Next Purchase Days] =
    CALCULATE(
        AVERAGE( 'mart vw_customer_health'[expected_next_purchase_days] ),
        NOT ISBLANK( 'mart vw_customer_health'[expected_next_purchase_days] )
    )

-- | Min Next Purchase Days | Shortest expected interval in filter context |
MEASURE '_Measures'[Min Next Purchase Days] =
    CALCULATE(
        MIN( 'mart vw_customer_health'[expected_next_purchase_days] ),
        NOT ISBLANK( 'mart vw_customer_health'[expected_next_purchase_days] )
    )

-- ================================================================================
-- SECTION 9: DIAGNOSTIC MEASURES
-- Validation checks. Prefix "_" to sort last in the field list.
-- Hide from report view once validation is confirmed.
-- ================================================================================

-- | _Data Validation | Checks all critical measures return non-blank values |
MEASURE '_Measures'[_Data Validation] =
    VAR HasGMV             = NOT ISBLANK( [Total GMV] )
    VAR HasCustomers       = [Total Customers] > 0
    VAR HasChurnProb       = NOT ISBLANK( [Avg Churn Probability] )
    VAR HasCLV             = NOT ISBLANK( [Avg CLV Predicted] )
    VAR HasSentiment       = NOT ISBLANK( [Avg Sentiment] )
    VAR HasActionQueue     = [Actionable Customers] > 0
    VAR HasGeo             = NOT ISBLANK( [National GMV] )
    RETURN
    SWITCH(
        TRUE(),
        NOT HasGMV,         "FAIL: 'mart vw_customer_health'[total_gmv] blank — check import",
        NOT HasCustomers,   "FAIL: No customers — check vw_customer_health",
        NOT HasChurnProb,   "FAIL: churn_probability blank — run churn_model.py",
        NOT HasCLV,         "FAIL: clv_predicted_6m blank — run clv_model.py",
        NOT HasSentiment,   "WARN: avg_sentiment_score blank — run sentiment.py",
        NOT HasActionQueue, "FAIL: No actions — run action_rules.py",
        NOT HasGeo,         "FAIL: GeoPerformance empty — check vw_geo_performance",
        "PASS — all checks cleared"
    )

-- | _Row Count CustomerHealth | Expect 96,096 |
MEASURE '_Measures'[_Row Count CustomerHealth] =
    COUNTROWS( 'mart vw_customer_health' )

-- | _Row Count SentimentScores | Expect ~99,224 |
MEASURE '_Measures'[_Row Count SentimentScores] =
    COUNTROWS( 'mart sentiment_scores' )

-- | _Row Count ActionQueue | Expect 96,096 |
MEASURE '_Measures'[_Row Count ActionQueue] =
    COUNTROWS( 'mart crm_action_queue' )

-- | _Null CLV Count | Expect ~24,910 (single-order customers) |
MEASURE '_Measures'[_Null CLV Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] ) )
    )

-- | _Null Sentiment Count | Expect ~56,332 (customers with no reviews) |
MEASURE '_Measures'[_Null Sentiment Count] =
    COUNTROWS(
        FILTER( 'mart vw_customer_health', ISBLANK( 'mart vw_customer_health'[avg_sentiment_score] ) )
    )

-- ================================================================================
-- SECTION 10: CALCULATED COLUMNS
-- These CANNOT be created via DAX Query View DEFINE syntax.
-- For each column below:
--   1. Go to Model view in Power BI
--   2. Select the 'mart vw_customer_health' table
--   3. Click "New column" in the ribbon
--   4. Paste the formula body only (the part after the = sign, without the MEASURE wrapper)
--   5. Name the column as indicated
-- ================================================================================

-- COLUMN: 'mart vw_customer_health'[CLV Band]
-- Table: 'mart vw_customer_health' | Sort by: 'mart vw_customer_health'[CLV Band Sort]
-- Formula body:
--   SWITCH(
--       TRUE(),
--       ISBLANK('mart vw_customer_health'[clv_predicted_6m]), "No CLV",
--       'mart vw_customer_health'[clv_predicted_6m] = 0, "R$0",
--       'mart vw_customer_health'[clv_predicted_6m] <= 1, "R$0.01–1",
--       'mart vw_customer_health'[clv_predicted_6m] <= 5, "R$1–5",
--       'mart vw_customer_health'[clv_predicted_6m] <= 20, "R$5–20",
--       'mart vw_customer_health'[clv_predicted_6m] <= 100, "R$20–100",
--       "R$100+"
--   )

-- COLUMN: 'mart vw_customer_health'[CLV Band Sort]
-- Table: 'mart vw_customer_health' | Use to sort CLV Band column
-- Formula body:
--   SWITCH(
--       'mart vw_customer_health'[CLV Band],
--       "No CLV",   0,
--       "R$0",      1,
--       "R$0.01–1", 2,
--       "R$1–5",    3,
--       "R$5–20",   4,
--       "R$20–100", 5,
--       "R$100+",   6,
--       0
--   )

-- COLUMN: 'mart vw_customer_health'[Churn Prob Bucket]
-- Table: 'mart vw_customer_health' | Sort by: 'mart vw_customer_health'[Churn Prob Bucket Sort]
-- Formula body:
--   SWITCH(
--       TRUE(),
--       'mart vw_customer_health'[churn_probability] < 0.1, "0.0–0.1",
--       'mart vw_customer_health'[churn_probability] < 0.2, "0.1–0.2",
--       'mart vw_customer_health'[churn_probability] < 0.3, "0.2–0.3",
--       'mart vw_customer_health'[churn_probability] < 0.4, "0.3–0.4",
--       'mart vw_customer_health'[churn_probability] < 0.5, "0.4–0.5",
--       'mart vw_customer_health'[churn_probability] < 0.6, "0.5–0.6",
--       'mart vw_customer_health'[churn_probability] < 0.7, "0.6–0.7",
--       'mart vw_customer_health'[churn_probability] < 0.8, "0.7–0.8",
--       'mart vw_customer_health'[churn_probability] < 0.9, "0.8–0.9",
--       "0.9–1.0"
--   )

-- COLUMN: 'mart vw_customer_health'[Churn Prob Bucket Sort]
-- Table: 'mart vw_customer_health' | Use to sort Churn Prob Bucket column
-- Formula body:
--   SWITCH(
--       'mart vw_customer_health'[Churn Prob Bucket],
--       "0.0–0.1", 1,  "0.1–0.2", 2,  "0.2–0.3", 3,
--       "0.3–0.4", 4,  "0.4–0.5", 5,  "0.5–0.6", 6,
--       "0.6–0.7", 7,  "0.7–0.8", 8,  "0.8–0.9", 9,
--       "0.9–1.0", 10,
--       0
--   )

-- COLUMN: 'mart vw_customer_health'[Is Churn Risk]
-- Table: 'mart vw_customer_health' | 1 = above deployed threshold, 0 = below
-- Formula body:
--   IF( 'mart vw_customer_health'[churn_probability] >= 0.6, 1, 0 )

-- COLUMN: 'mart vw_customer_health'[CLV Churn Quadrant]
-- Table: 'mart vw_customer_health' | Used in CLV vs churn scatter on P5
-- NOTE: Replace 0.93 with your actual [Median CLV] value if it differs.
-- Formula body:
--   SWITCH(
--       TRUE(),
--       'mart vw_customer_health'[churn_probability] >= 0.6
--           && NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
--           && 'mart vw_customer_health'[clv_predicted_6m] >= 0.93,
--           "Retain",
--       'mart vw_customer_health'[churn_probability] < 0.6
--           && NOT ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
--           && 'mart vw_customer_health'[clv_predicted_6m] >= 0.93,
--           "Grow",
--       'mart vw_customer_health'[churn_probability] >= 0.6
--           && ( ISBLANK( 'mart vw_customer_health'[clv_predicted_6m] )
--                || 'mart vw_customer_health'[clv_predicted_6m] < 0.93 ),
--           "Reactivate",
--       "Monitor"
--   )

-- COLUMN: 'mart vw_customer_health'[Priority Sort]
-- Table: 'mart vw_customer_health' | Sorts action priority: HIGH → MED → LOW in tables
-- Formula body:
--   SWITCH(
--       'mart vw_customer_health'[latest_action_priority],
--       "HIGH", 1,
--       "MED",  2,
--       "LOW",  3,
--       4
--   )

-- ================================================================================
-- EXECUTION BLOCK
-- This EVALUATE runs a validation query and returns results to DAX Query View.
-- All measures defined above are evaluated in one pass.
-- ================================================================================

EVALUATE
ROW(
    "Module",                   "_CRM Measures",
    "Status",                   [_Data Validation],
    "Total GMV (R$)",           [Total GMV],
    "Total Customers",          [Total Customers],
    "Churn Rate",               [Churn Rate],
    "Avg Churn Probability",    [Avg Churn Probability],
    "Avg Health Score",         [Avg Health Score],
    "HIGH Priority Actions",    [HIGH Priority Count],
    "Actionable Customers",     [Actionable Customers],
    "Actionable %",             [Actionable Pct],
    "Flagged GMV (R$)",         [Flagged GMV],
    "CLV Coverage %",           [CLV Coverage Pct],
    "Median CLV (R$)",          [Median CLV],
    "Avg Sentiment Score",      [Avg Sentiment],
    "Positive Sentiment %",     [Pct Positive Sentiment],
    "Sarcasm Flag Count",       [Sarcasm Flag Count],
    "CustomerHealth Rows",      [_Row Count CustomerHealth],
    "ActionQueue Rows",         [_Row Count ActionQueue],
    "Null CLV Count",           [_Null CLV Count]
)
```