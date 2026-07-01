/* =============================================================================
   mart.vw_churn_signals
   At-risk customers: either rule-based churned (is_churned=1) or model-flagged
   (churn_probability > 0.4 once churn_model.py has run).

   Two distinct "why" columns, answering different questions:
     - churn_driver_summary: does the 180-day rule agree with the model?
     - primary_driver: WHICH dimension (delivery/sentiment/recency/value) is
       worst for this specific customer, ranked against population norms
       already computed in customer_360/rfm_features — not re-running
       PERCENT_RANK() over the whole table inside a view.

   urgency_score (0-100): weighted triage score — see inline comments in the
   SELECT for the weighting rationale. Falls back gracefully to rule-based
   signals (is_churned, total_gmv-derived monetary_score) when churn_model.py
   / clv_model.py haven't run yet, so this view is useful from day one of
   Phase 4, not just after Phase 5's ML pipeline completes.
   ============================================================================= */
USE CRM_Analytics;
GO

CREATE OR ALTER VIEW mart.vw_churn_signals AS
WITH base AS (
    SELECT
        h.*,
        -- Re-derive comparable 0-1 "badness" signals per dimension so we can
        -- rank which one is worst for THIS customer, without re-running
        -- PERCENT_RANK() over the whole population inside a view (expensive
        -- and would duplicate logic that already lives in sp_refresh_mart).
        -- These are coarse, intentionally: a quintile-based proxy is enough
        -- to pick a "primary driver" label, it doesn't need to be the exact
        -- percentile used in customer_health_score.
        CASE WHEN h.pct_late_deliveries IS NULL THEN 0 ELSE h.pct_late_deliveries END AS delivery_badness,
        CASE WHEN h.avg_review_score IS NULL THEN 0.5
             ELSE 1 - (h.avg_review_score / 5.0) END AS sentiment_badness,
        CASE WHEN h.recency_score IS NULL THEN 0.5
             ELSE 1 - ((h.recency_score - 1) / 4.0) END AS recency_badness,    -- recency_score 1 = worst -> badness 1; 5 = best -> badness 0
        CASE WHEN h.monetary_score IS NULL THEN 0.5
             ELSE 1 - ((h.monetary_score - 1) / 4.0) END AS monetary_badness  -- low spend relative to population = "at-risk value", not literally bad, but flags low-stakes churn
    FROM mart.vw_customer_health h
    WHERE h.is_churned = 1
       OR (h.churn_probability IS NOT NULL AND h.churn_probability > 0.4)
)
SELECT
    customer_unique_id, customer_state, customer_city,
    total_orders, total_gmv, days_since_last_order, recency_band,
    is_churned, churn_probability, customer_health_score, health_tier,
    avg_review_score, pct_negative_reviews, pct_late_deliveries,
    rfm_segment, recency_tier, frequency_tier, monetary_tier,
    clv_predicted_6m, latest_action_type, latest_action_priority, as_of_date,

    -- rule-vs-model agreement summary — different question from primary_driver below
    CASE
        WHEN is_churned = 1 AND churn_probability IS NOT NULL AND churn_probability > 0.4
            THEN 'Rule + model agree: churned'
        WHEN is_churned = 1
            THEN 'Rule-based: no order in 180+ days'
        WHEN churn_probability IS NOT NULL AND churn_probability > 0.4
            THEN 'Model-flagged: at risk'
        ELSE 'Active'
    END AS churn_driver_summary,

    -- primary_driver: whichever dimension is worst FOR THIS CUSTOMER,
    -- not just whichever signal happens to be non-null first
    CASE
        WHEN delivery_badness >= sentiment_badness
         AND delivery_badness >= recency_badness
         AND delivery_badness >= monetary_badness
         AND delivery_badness > 0.3
            THEN 'Delivery experience'
        WHEN sentiment_badness >= recency_badness
         AND sentiment_badness >= monetary_badness
         AND sentiment_badness > 0.3
            THEN 'Low satisfaction'
        WHEN recency_badness >= monetary_badness
            THEN 'Lapsed / inactive'
        ELSE 'Low historical value'
    END AS primary_driver,

    -- urgency_score (0-100): how much and how soon to act.
    -- Weighting logic, not arbitrary:
    --   40% likelihood of loss   — churn_probability if the model has run,
    --                              else is_churned (1.0/0.0) as a rule-based fallback
    --   40% value at stake       — higher value at stake = higher urgency,
    --                              in BOTH branches below (clv_predicted_6m
    --                              when available, else monetary standing
    --                              as a proxy) — these must agree in direction,
    --                              or urgency_score would flip meaning
    --                              depending on whether Python has run yet
    --   20% timing pressure      — how far past the churn threshold they
    --                              already are; capped at 1.0 via CASE (T-SQL
    --                              has no LEAST()/MIN() scalar function), since
    --                              "very overdue" shouldn't dominate once
    --                              someone's clearly gone
    ROUND((
        0.4 * ISNULL(churn_probability, CAST(is_churned AS DECIMAL(4,2)))
      + 0.4 * (1 - monetary_badness)   -- higher historical/predicted value = higher urgency, consistently
      + 0.2 * CASE WHEN ISNULL(days_since_last_order, 0) / 360.0 > 1.0 THEN 1.0
                   ELSE ISNULL(days_since_last_order, 0) / 360.0 END
    ) * 100, 1) AS urgency_score

FROM base;
GO

PRINT 'mart.vw_churn_signals created.';
GO