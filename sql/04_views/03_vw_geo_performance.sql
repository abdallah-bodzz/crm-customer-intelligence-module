/* =============================================================================
   mart.vw_geo_performance
   State-level aggregates for the Power BI geo dashboard page. Pre-aggregated
   so the map visual needs no DAX measures for the base values — only
   formatting/color rules.
   Per Phase 2 EDA's locked decision: states <2% of total GMV collapse into
   'Other' in the dashboard layer — that collapsing happens here in SQL,
   not in DAX, so it's consistent across every visual that reads this view.

   NOT included: YoY / month-over-month trend columns. Doing that correctly
   requires historical mart snapshots (a snapshot table this project hasn't
   built), not a join inside a stateless view. Faking it with a same-table
   self-comparison would produce a meaningless number, not a real trend —
   that's worse than no column at all. If trending becomes a real
   requirement, it's a new mart.customer_360_snapshot table + a daily/weekly
   capture job, not a view-layer trick.
   ============================================================================= */
USE CRM_Analytics;
GO

CREATE OR ALTER VIEW mart.vw_geo_performance AS
WITH state_agg AS (
    SELECT
        ISNULL(customer_state, 'UNKNOWN')          AS customer_state,
        COUNT(*)                                    AS customer_count,
        SUM(total_gmv)                               AS total_gmv,
        AVG(avg_delivery_delta_days)                  AS avg_delivery_delta_days,
        AVG(CASE WHEN pct_late_deliveries > 0 THEN pct_late_deliveries ELSE 0 END) AS avg_pct_late,
        SUM(CASE WHEN is_churned = 1 THEN 1 ELSE 0 END) AS churned_count,
        AVG(customer_health_score)                       AS avg_health_score
    FROM mart.customer_360
    GROUP BY customer_state
),
totals AS (
    SELECT SUM(total_gmv) AS grand_total_gmv, SUM(customer_count) AS grand_total_customers
    FROM state_agg
)
SELECT
    sa.customer_state,
    sa.customer_count,
    sa.total_gmv,
    CAST(sa.total_gmv / t.grand_total_gmv * 100 AS DECIMAL(6,2))           AS pct_of_total_gmv,
    CAST(sa.customer_count * 1.0 / t.grand_total_customers * 100 AS DECIMAL(6,2)) AS pct_of_total_customers,
    sa.avg_delivery_delta_days,
    CAST(sa.avg_pct_late * 100 AS DECIMAL(6,2))                              AS pct_late_deliveries,
    CAST(sa.churned_count * 1.0 / sa.customer_count * 100 AS DECIMAL(6,2))     AS churn_rate_pct,
    sa.avg_health_score,
    CASE WHEN sa.total_gmv / t.grand_total_gmv < 0.02 THEN 'Other' ELSE sa.customer_state END AS dashboard_state_label,
    rl.as_of_date
FROM state_agg sa
CROSS JOIN totals t
CROSS JOIN mart.refresh_log rl;
GO

PRINT 'mart.vw_geo_performance created.';
GO