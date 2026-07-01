/* =============================================================================
   mart.rfm_features
   Source: mart.customer_360
   Grain: one row per customer_unique_id.
   SQL computes raw R/F/M values and NTILE(5) quintile scores.
   Python (segmentation.py) reads this table and writes back rfm_segment
   and km_cluster — SQL never assigns business labels.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('mart.rfm_features', 'U') IS NOT NULL
    DROP TABLE mart.rfm_features;
GO

CREATE TABLE mart.rfm_features (
    customer_unique_id     VARCHAR(50)     NOT NULL,

    recency_days             INT             NOT NULL,   -- DATEDIFF(DAY, last_order_date, @as_of_date) — same @as_of_date as customer_360
    frequency                 INT             NOT NULL,   -- = total_orders
    monetary                   DECIMAL(14,2)   NOT NULL,   -- = total_gmv

    recency_score                TINYINT         NOT NULL,   -- NTILE(5), 5 = most recent
    frequency_score                TINYINT         NOT NULL,   -- NTILE(5), 5 = most frequent
    monetary_score                  TINYINT         NOT NULL,   -- NTILE(5), 5 = highest spend
    rfm_score                         VARCHAR(3)      NOT NULL,   -- concatenation, e.g. '555', '132'

    -- Python-filled
    rfm_segment                         NVARCHAR(30)    NULL,   -- e.g. Champions, Loyal, At Risk, Hibernating, Lost
    km_cluster                            TINYINT         NULL,

    refreshed_at                            DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_rfm_features PRIMARY KEY (customer_unique_id)
);
GO

CREATE INDEX ix_rfm_features_score ON mart.rfm_features (rfm_score);
CREATE INDEX ix_rfm_features_segment ON mart.rfm_features (rfm_segment);
GO

PRINT 'mart.rfm_features created.';
GO
