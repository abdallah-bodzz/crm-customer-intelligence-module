/* =============================================================================
   mart.sentiment_scores
   Source: warehouse.dim_review (skeleton only — SQL provides the clean,
   complete row set; sentiment.py reads review_comment_message, runs VADER,
   writes compound_score + sentiment_label back to this same table).
   Grain: one row per review_id (already deduplicated in Silver).
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('mart.sentiment_scores', 'U') IS NOT NULL
    DROP TABLE mart.sentiment_scores;
GO

CREATE TABLE mart.sentiment_scores (
    review_id                  VARCHAR(50)     NOT NULL,
    order_id                    VARCHAR(50)     NOT NULL,
    customer_unique_id            VARCHAR(50)     NOT NULL,

    review_score                    TINYINT         NOT NULL,
    review_comment_message            NVARCHAR(MAX)   NULL,   -- VADER only runs where this is non-empty
    review_creation_date                DATETIME2(3)    NULL,

    -- Python-filled
    compound_score                        DECIMAL(6,4)    NULL,   -- VADER compound, -1.0 to 1.0
    sentiment_label                         VARCHAR(10)     NULL,   -- positive / neutral / negative

    refreshed_at                              DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_sentiment_scores PRIMARY KEY (review_id)
);
GO

CREATE INDEX ix_sentiment_scores_customer ON mart.sentiment_scores (customer_unique_id);
GO

PRINT 'mart.sentiment_scores created.';
GO
