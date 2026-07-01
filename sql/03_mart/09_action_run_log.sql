/* =============================================================================
   CRM Customer Intelligence Module — Action Run Log DDL
   =============================================================================
   Schema    : mart
   Table     : action_run_log
   Purpose   : One row per action_rules.py execution. Preserves the full
               audit history of every action queue rebuild: when it ran,
               which thresholds were active, how many customers landed in
               each bucket.

   This is the history table. mart.crm_action_queue is the current-state
   snapshot. They are complementary — don't conflate them.

   Run order : After 05_crm_action_queue.sql (mart schema must exist)
   Idempotent: Yes — IF NOT EXISTS guard.
   ============================================================================= */

USE CRM_Analytics;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'mart' AND t.name = 'action_run_log'
)
BEGIN
    CREATE TABLE mart.action_run_log (
        run_id                  INT IDENTITY(1,1)   NOT NULL,

        -- When and who
        run_timestamp           DATETIME2(3)        NOT NULL  DEFAULT SYSUTCDATETIME(),
        run_by                  NVARCHAR(100)       NULL,     -- os.getlogin() from Python; NULL if unavailable
        script_version          VARCHAR(20)         NULL,     -- semver string from action_rules.py.__version__

        -- Thresholds actually used this run (CLI overrides reflected here, not just config defaults)
        churn_threshold_used    FLOAT               NOT NULL,
        clv_percentile_used     INT                 NOT NULL,
        vip_percentile_used     INT                 NOT NULL,
        write_mode              VARCHAR(30)         NOT NULL, -- 'TRUNCATE_INSERT' or 'DRY_RUN'

        -- Counts per action type (denormalised for fast dashboard queries)
        n_retention_campaign    INT                 NOT NULL  DEFAULT 0,
        n_reactivation          INT                 NOT NULL  DEFAULT 0,
        n_vip_upgrade           INT                 NOT NULL  DEFAULT 0,
        n_at_risk_nurture       INT                 NOT NULL  DEFAULT 0,
        n_monitor               INT                 NOT NULL  DEFAULT 0,
        n_total                 INT                 NOT NULL  DEFAULT 0,

        -- Priority breakdown
        n_priority_high         INT                 NOT NULL  DEFAULT 0,
        n_priority_med          INT                 NOT NULL  DEFAULT 0,
        n_priority_low          INT                 NOT NULL  DEFAULT 0,

        -- Coverage sanity
        n_customers_in          INT                 NOT NULL, -- rows read from vw_customer_health
        n_customers_unmatched   INT                 NOT NULL  DEFAULT 0, -- should always be 0; alert if not

        -- Full config snapshot (JSON) — lets you reconstruct exactly what rules ran
        config_snapshot         NVARCHAR(MAX)       NULL,

        -- Notes / error summary
        run_notes               NVARCHAR(500)       NULL,

        CONSTRAINT pk_action_run_log PRIMARY KEY CLUSTERED (run_id)
    );

    EXEC sys.sp_addextendedproperty
        @name  = N'MS_Description',
        @value = N'Audit log: one row per action_rules.py execution. Preserves threshold history and count breakdowns. crm_action_queue is the current-state snapshot; this table is the history.',
        @level0type = N'SCHEMA', @level0name = N'mart',
        @level1type = N'TABLE',  @level1name = N'action_run_log';

    PRINT 'Created mart.action_run_log';
END
ELSE
BEGIN
    PRINT 'mart.action_run_log already exists — skipping.';
END
GO

-- Useful index: last-N-runs query pattern for Power BI audit page
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes 
    WHERE name = 'ix_action_run_log_timestamp' 
    AND object_id = OBJECT_ID('mart.action_run_log')
)
BEGIN
    CREATE NONCLUSTERED INDEX ix_action_run_log_timestamp
        ON mart.action_run_log (run_timestamp DESC)
        INCLUDE (n_total, churn_threshold_used, clv_percentile_used, write_mode);
END
GO

PRINT 'action_run_log DDL complete.';
GO