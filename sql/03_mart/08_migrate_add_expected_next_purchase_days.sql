/* =============================================================================
   Migration: add expected_next_purchase_days to mart.customer_360

   WHY THIS FILE EXISTS:
   The original 01_customer_360.sql DDL was missing this column — a real
   miss confirmed by an actual pipeline run: next_purchase.py wrote
   correctly throughout development and testing, but the column it
   targets was never added to the table definition, so SQL Server
   rejected the UPDATE with "Invalid column name 'expected_next_purchase_days'."

   DO NOT fix this by re-running 01_customer_360.sql. That script DROPs
   and recreates the table — at this point your database already has
   real output from a successful full pipeline run (sentiment, segment,
   clv, churn all completed and wrote real predictions). Re-running the
   DROP-based DDL would destroy all of that. Run THIS file instead — it
   only adds the missing column to the table that already exists, with
   no data loss.

   AFTER running this file once, 01_customer_360.sql has ALSO been
   updated (this column is now in its DDL) so any FUTURE from-scratch
   build of the database is correct on the first try. This migration is
   only needed for a database that was built BEFORE that fix.
   ============================================================================= */
USE CRM_Analytics;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('mart.customer_360')
      AND name = 'expected_next_purchase_days'
)
BEGIN
    ALTER TABLE mart.customer_360
    ADD expected_next_purchase_days DECIMAL(10,1) NULL;

    PRINT 'Added expected_next_purchase_days to mart.customer_360.';
END
ELSE
BEGIN
    PRINT 'expected_next_purchase_days already exists on mart.customer_360 — no action taken.';
END
GO

/* Sanity check — confirm the column is there and currently NULL for
   everyone (expected, since next_purchase.py failed before writing
   anything in the run that surfaced this bug — it crashed on the FIRST
   batch's UPDATE, so zero rows were written, nothing to reconcile). */
SELECT
    COUNT(*) AS total_customers,
    SUM(CASE WHEN expected_next_purchase_days IS NULL THEN 1 ELSE 0 END) AS still_null
FROM mart.customer_360;
GO
