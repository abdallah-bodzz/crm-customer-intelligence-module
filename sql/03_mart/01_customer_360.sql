/* =============================================================================
   mart.customer_360 — the CRM "account record"
   Source: warehouse.dim_customer (current) + fact_orders + fact_order_items
           + dim_review (aggregated per order_id first — see note below).
   Grain: one row per customer_unique_id.
   No FKs to warehouse — mart is fully rebuilt on every refresh; integrity
   guarantees live in Silver, not here. Keeps TRUNCATE simple and safe.
   ============================================================================= */
USE CRM_Analytics;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'mart')
    EXEC('CREATE SCHEMA mart');
GO

IF OBJECT_ID('mart.customer_360', 'U') IS NOT NULL
    DROP TABLE mart.customer_360;
GO

CREATE TABLE mart.customer_360 (
    customer_unique_id          VARCHAR(50)     NOT NULL,
    customer_state               VARCHAR(5)      NULL,
    customer_city                 NVARCHAR(150)   NULL,

    total_orders                  INT             NOT NULL,
    total_gmv                      DECIMAL(14,2)   NOT NULL,
    avg_order_value                 DECIMAL(12,2)   NOT NULL,
    total_freight_paid               DECIMAL(14,2)   NOT NULL DEFAULT 0,  -- cost-driver lens: high GMV + high freight ratio = less profitable than it looks

    first_order_date                DATE            NULL,
    last_order_date                  DATE            NULL,
    tenure_days                       INT             NULL,   -- last_order_date - first_order_date
    days_since_last_order              INT             NULL,   -- vs @as_of_date at refresh time

    avg_review_score                   DECIMAL(4,2)    NULL,   -- averaged per order_id first, then across orders
    pct_negative_reviews                 DECIMAL(6,4)    NULL,   -- share of orders with avg review_score <= 2

    avg_delivery_delta_days               DECIMAL(8,2)    NULL,
    pct_late_deliveries                     DECIMAL(6,4)    NULL,

    is_churned                               BIT             NOT NULL,   -- days_since_last_order > churn_window_days; single source of truth, churn_model.py reads this, never recomputes it
    customer_health_score                      DECIMAL(6,2)    NOT NULL,  -- 0-100, percentile-rank composite — see sp_refresh_mart for formula
    health_tier                                  AS (
        CASE
            WHEN customer_health_score >= 75 THEN 'High'
            WHEN customer_health_score >= 50 THEN 'Medium'
            ELSE 'Low'
        END
    ) PERSISTED,   -- computed FROM customer_health_score, not stored independently — cannot drift out of sync with the score

    -- Python-filled, NULL until the relevant model runs
    churn_probability                            DECIMAL(6,4)    NULL,
    clv_predicted_6m                              DECIMAL(14,2)   NULL,
    avg_sentiment_score                            DECIMAL(6,4)    NULL,
    expected_next_purchase_days                     DECIMAL(10,1)   NULL,   -- Weibull AFT median, next_purchase.py. NULL for single-order customers (out of scope for that model — see its module docstring) and for repeat customers whose survival curve never crosses 50% probability within the model's horizon.

    refreshed_at                                    DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_customer_360 PRIMARY KEY (customer_unique_id)
);
GO

CREATE INDEX ix_customer_360_state         ON mart.customer_360 (customer_state);
CREATE INDEX ix_customer_360_health        ON mart.customer_360 (customer_health_score);
CREATE INDEX ix_customer_360_health_tier   ON mart.customer_360 (health_tier);
CREATE INDEX ix_customer_360_is_churned    ON mart.customer_360 (is_churned);
GO

PRINT 'mart.customer_360 created.';
GO