/* =============================================================================
   mart.crm_action_queue
   DDL ONLY. This table is never touched by sp_refresh_mart — Python (run.py)
   owns it completely: truncates and rewrites it after every model run based
   on churn_probability, clv_predicted_6m, rfm_segment, and is_churned.
   This is the table that makes the project behave like a real CRM system —
   the output of analytics here is tasks/records, not just a report.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('mart.crm_action_queue', 'U') IS NOT NULL
    DROP TABLE mart.crm_action_queue;
GO

CREATE TABLE mart.crm_action_queue (
    action_id              INT IDENTITY(1,1) NOT NULL,

    customer_unique_id       VARCHAR(50)     NOT NULL,
    action_type                VARCHAR(30)     NOT NULL,   -- RETENTION_CAMPAIGN, REACTIVATION, VIP_UPGRADE, MONITOR
    priority                     VARCHAR(10)     NOT NULL,   -- HIGH / MED / LOW

    churn_probability               DECIMAL(6,4)    NULL,
    clv_predicted                     DECIMAL(14,2)   NULL,
    trigger_reason                      NVARCHAR(300)   NOT NULL,   -- human-readable: why this action fired

    created_at                            DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_crm_action_queue PRIMARY KEY (action_id),
    CONSTRAINT chk_action_type CHECK (action_type IN ('RETENTION_CAMPAIGN', 'REACTIVATION', 'VIP_UPGRADE', 'MONITOR')),
    CONSTRAINT chk_action_priority CHECK (priority IN ('HIGH', 'MED', 'LOW'))
);
GO

CREATE INDEX ix_action_queue_customer ON mart.crm_action_queue (customer_unique_id);
CREATE INDEX ix_action_queue_priority ON mart.crm_action_queue (priority, action_type);
GO

PRINT 'mart.crm_action_queue created (structure only — Python owns the data).';
GO
