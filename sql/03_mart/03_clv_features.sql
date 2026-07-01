/* =============================================================================
   mart.clv_features
   Source: mart.customer_360 + warehouse.fact_order_items + warehouse.dim_product
           + warehouse.fact_orders (for preferred_payment_type)
   Grain: one row per customer_unique_id.
   actual_gmv_post_cutoff is the ML TARGET VARIABLE for clv_model.py.
   Cutoff is 2018-05-01 (the EDA-locked value) — NOT 2018-09-01, which the
   Phase 2 EDA explicitly killed for leaving ~0 test rows. Sourced from a
   single @ml_cutoff_date variable in sp_refresh_mart, never hardcoded twice.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('mart.clv_features', 'U') IS NOT NULL
    DROP TABLE mart.clv_features;
GO

CREATE TABLE mart.clv_features (
    customer_unique_id              VARCHAR(50)     NOT NULL,

    avg_order_value                   DECIMAL(12,2)   NOT NULL,
    order_frequency_per_month           DECIMAL(10,4)   NULL,   -- NULL only if tenure_days = 0 (single-day customer) — see NULLIF guard in ETL
    tenure_months                         DECIMAL(10,2)   NULL,
    total_categories_purchased              INT             NOT NULL,

    avg_review_score                          DECIMAL(4,2)    NULL,
    avg_delivery_delta                          DECIMAL(8,2)    NULL,
    pct_late                                      DECIMAL(6,4)    NULL,
    customer_state                                  VARCHAR(5)      NULL,
    days_since_last_order                             INT             NULL,   -- recency; strongest single churn predictor, belongs in the CLV feature matrix explicitly

    preferred_payment_type                            VARCHAR(30)     NULL,
    -- tiebreak: most frequent payment_type by order count, ties broken by
    -- total payment_value DESC, final tiebreak alphabetical on payment_type
    -- for full determinism — same discipline as the Silver review_id dedup.

    actual_gmv_post_cutoff                              DECIMAL(14,2)   NOT NULL,   -- TARGET VARIABLE, cutoff = @ml_cutoff_date (2018-05-01)

    -- Python-filled
    clv_predicted_6m                                      DECIMAL(14,2)   NULL,
    clv_ci_lower                                            DECIMAL(14,2)   NULL,
    clv_ci_upper                                              DECIMAL(14,2)   NULL,

    refreshed_at                                                DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_clv_features PRIMARY KEY (customer_unique_id)
);
GO

CREATE INDEX ix_clv_features_state ON mart.clv_features (customer_state);
CREATE INDEX ix_clv_features_recency ON mart.clv_features (days_since_last_order);
GO

PRINT 'mart.clv_features created.';
GO
