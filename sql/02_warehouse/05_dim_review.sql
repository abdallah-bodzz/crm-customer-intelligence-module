/* =============================================================================
   warehouse.dim_review
   Source: staging.stg_order_reviews
   Sentiment (VADER) is NOT computed here — that's Gold/Python's job per the
   project's medallion contract. Silver stores clean text + a has_comment
   flag only. 58.71% of reviews have no text (locked EDA finding) — that's
   expected, not a data quality bug.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('warehouse.dim_review', 'U') IS NOT NULL
    DROP TABLE warehouse.dim_review;
GO

CREATE TABLE warehouse.dim_review (
    review_sk                INT IDENTITY(1,1) NOT NULL,
    review_id                 VARCHAR(50)   NOT NULL,
    order_id                  VARCHAR(50)   NOT NULL,
    customer_unique_id        VARCHAR(50)   NOT NULL,   -- denormalized: avoids the dim_review -> fact_orders -> dim_customer hop for sentiment rollups
    review_score              TINYINT       NOT NULL,
    review_comment_title      NVARCHAR(200) NULL,
    review_comment_message    NVARCHAR(MAX) NULL,
    has_comment                AS (
        CASE WHEN review_comment_message IS NOT NULL AND LEN(review_comment_message) > 0
             THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END
    ) PERSISTED,
    review_creation_date      DATETIME2(3)  NULL,
    review_answer_timestamp   DATETIME2(3)  NULL,
    response_delay_days        AS (
        DATEDIFF(DAY, review_creation_date, review_answer_timestamp)
    ) PERSISTED,
    created_at                 DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_dim_review PRIMARY KEY (review_sk),
    CONSTRAINT uq_dim_review_id UNIQUE (review_id),
    CONSTRAINT chk_dim_review_score CHECK (review_score BETWEEN 1 AND 5)
);
GO

CREATE INDEX ix_dim_review_order_id ON warehouse.dim_review (order_id);
CREATE INDEX ix_dim_review_customer ON warehouse.dim_review (customer_unique_id);
CREATE INDEX ix_dim_review_score    ON warehouse.dim_review (review_score);
GO

PRINT 'warehouse.dim_review created.';
GO
