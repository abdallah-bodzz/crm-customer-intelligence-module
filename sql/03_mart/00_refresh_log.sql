/* =============================================================================
   mart.refresh_log
   Single source of truth for "what as_of_date / cutoff did the LAST refresh
   use" — written once by sp_refresh_mart, read by every view that needs to
   expose "data as of" to Power BI. Views cannot read a stored procedure's
   local variables, and a view independently computing MAX(order_purchase_
   timestamp) would create a second clock that can drift from customer_360's
   — same class of bug already fixed twice in this project (Silver SCD2,
   Gold RFM/customer_360 sharing one @as_of_date). This table closes that
   gap for the view layer.
   Grain: one row, always overwritten (not appended) — only the latest
   refresh's metadata matters for "as of" display purposes.
   ============================================================================= */
USE CRM_Analytics;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'mart')
    EXEC('CREATE SCHEMA mart');
GO

IF OBJECT_ID('mart.refresh_log', 'U') IS NOT NULL
    DROP TABLE mart.refresh_log;
GO

CREATE TABLE mart.refresh_log (
    refresh_id        INT NOT NULL DEFAULT 1,   -- always 1; single-row table, enforced below
    as_of_date         DATE NOT NULL,
    ml_cutoff_date       DATE NOT NULL,
    churn_window_days     INT  NOT NULL,
    refreshed_at            DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_refresh_log PRIMARY KEY (refresh_id),
    CONSTRAINT chk_refresh_log_single_row CHECK (refresh_id = 1)
);
GO

PRINT 'mart.refresh_log created.';
GO
