/* =============================================================================
   mart.vw_customer_health
   Flat profile view for Power BI — one row per customer_unique_id, joining
   customer_360 + rfm_features + clv_features + the latest crm_action_queue
   entry (if any) + mart.refresh_log for a shared "as of" date.
   This is a VIEW, not a table — always reflects the current state of the
   underlying mart tables, no separate refresh needed.
   Depends on mart.refresh_log existing (00_refresh_log.sql) and being
   populated by sp_refresh_mart before this view is queried.
   ============================================================================= */
USE CRM_Analytics;
GO

CREATE OR ALTER VIEW mart.vw_customer_health AS
SELECT
    c360.customer_unique_id,
    c360.customer_state,
    c360.customer_city,
    c360.total_orders,
    c360.total_gmv,
    c360.total_freight_paid,
    c360.avg_order_value,
    c360.first_order_date,
    c360.last_order_date,
    c360.tenure_days,
    c360.days_since_last_order,

    -- recency band for Power BI slicers — coarser cut than rfm.recency_score,
    -- useful when a user wants "show me 90+ day lapsed customers" without
    -- thinking in quintiles
    CASE
        WHEN c360.days_since_last_order <= 30  THEN '0-30 days'
        WHEN c360.days_since_last_order <= 90  THEN '31-90 days'
        WHEN c360.days_since_last_order <= 180 THEN '91-180 days'
        ELSE '180+ days'
    END AS recency_band,

    c360.avg_review_score,
    c360.pct_negative_reviews,
    c360.avg_delivery_delta_days,
    c360.pct_late_deliveries,
    c360.is_churned,
    c360.customer_health_score,
    c360.health_tier,
    c360.churn_probability,
    clv.clv_predicted_6m,
    c360.avg_sentiment_score,

    rfm.rfm_score,
    rfm.rfm_segment,
    rfm.km_cluster,
    rfm.recency_score,
    rfm.frequency_score,
    rfm.monetary_score,

    -- High/Medium/Low labels DERIVED from the existing 1-5 NTILE scores,
    -- not recomputed independently — cannot drift from rfm_features.
    -- (1-5 score -> 3-bucket label: 4-5 High, 3 Medium, 1-2 Low)
    CASE WHEN rfm.recency_score   >= 4 THEN 'High' WHEN rfm.recency_score   = 3 THEN 'Medium' ELSE 'Low' END AS recency_tier,
    CASE WHEN rfm.frequency_score >= 4 THEN 'High' WHEN rfm.frequency_score = 3 THEN 'Medium' ELSE 'Low' END AS frequency_tier,
    CASE WHEN rfm.monetary_score  >= 4 THEN 'High' WHEN rfm.monetary_score  = 3 THEN 'Medium' ELSE 'Low' END AS monetary_tier,

    clv.actual_gmv_post_cutoff,
    clv.clv_ci_lower,
    clv.clv_ci_upper,
    clv.preferred_payment_type,
    clv.total_categories_purchased,

    latest_action.action_type   AS latest_action_type,
    latest_action.priority      AS latest_action_priority,
    latest_action.trigger_reason AS latest_action_reason,
    latest_action.created_at    AS latest_action_date,

    rl.as_of_date   -- shared clock from mart.refresh_log; never recomputed here

FROM mart.customer_360 c360
LEFT JOIN mart.rfm_features rfm ON rfm.customer_unique_id = c360.customer_unique_id
LEFT JOIN mart.clv_features clv ON clv.customer_unique_id = c360.customer_unique_id
LEFT JOIN (
    -- most recent action per customer only — crm_action_queue can carry
    -- history if Python appends rather than replaces; this view always
    -- shows the latest, not every action ever queued
    SELECT
        customer_unique_id, action_type, priority, trigger_reason, created_at,
        ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY created_at DESC) AS rn
    FROM mart.crm_action_queue
) latest_action ON latest_action.customer_unique_id = c360.customer_unique_id AND latest_action.rn = 1
CROSS JOIN mart.refresh_log rl;
GO

PRINT 'mart.vw_customer_health created.';
GO