/* =============================================================================
   warehouse.fact_orders
   Source: staging.stg_orders, enriched with payment aggregates from
   staging.stg_order_payments and FKs to dim_customer / dim_date.
   Grain: one row per order_id.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('warehouse.fact_orders', 'U') IS NOT NULL
    DROP TABLE warehouse.fact_orders;
GO

CREATE TABLE warehouse.fact_orders (
    order_sk                       INT IDENTITY(1,1) NOT NULL,

    -- FKs
    customer_sk                    INT           NOT NULL,
    order_purchase_date_sk         INT           NOT NULL,   -- FK'd to 19000101 sentinel row if purchase_timestamp is ever NULL — never a NULL FK

    -- natural keys, kept for downstream joins
    order_id                       VARCHAR(50)   NOT NULL,
    customer_unique_id             VARCHAR(50)   NOT NULL,
    customer_id                    VARCHAR(50)   NOT NULL,

    order_status                   VARCHAR(20)   NULL,
    is_delivered                    AS (CASE WHEN order_status = 'delivered' THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END) PERSISTED,
    is_canceled                     AS (CASE WHEN order_status = 'canceled'  THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END) PERSISTED,

    order_purchase_timestamp       DATETIME2(3)  NULL,
    order_approved_at              DATETIME2(3)  NULL,
    order_delivered_carrier_date   DATETIME2(3)  NULL,
    order_delivered_customer_date  DATETIME2(3)  NULL,
    order_estimated_delivery_date  DATETIME2(3)  NULL,

    -- derived, persisted (computed once at load, not recomputed by Power BI)
    delivery_delta_days             AS (
        DATEDIFF(DAY, order_estimated_delivery_date, order_delivered_customer_date)
    ) PERSISTED,                                   -- negative = early, positive = late
    is_late                          AS (
        CASE WHEN order_delivered_customer_date > order_estimated_delivery_date
             THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END
    ) PERSISTED,
    approval_delay_hours            AS (
        DATEDIFF(HOUR, order_purchase_timestamp, order_approved_at)
    ) PERSISTED,

    -- payment aggregates (one order can have multiple payment rows — e.g. voucher + credit card)
    total_payment_value            DECIMAL(12,2) NULL,
    payment_type_primary           VARCHAR(30)   NULL,    -- type of the lowest payment_sequential row
    payment_installments_max       INT           NULL,
    payment_methods_count          INT           NULL,    -- distinct payment_type count, e.g. split payments

    created_at                     DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_fact_orders PRIMARY KEY (order_sk),
    CONSTRAINT uq_fact_orders_order_id UNIQUE (order_id),
    CONSTRAINT fk_fact_orders_customer FOREIGN KEY (customer_sk)
        REFERENCES warehouse.dim_customer (customer_sk),
    CONSTRAINT fk_fact_orders_date FOREIGN KEY (order_purchase_date_sk)
        REFERENCES warehouse.dim_date (date_sk)
);
GO

CREATE INDEX ix_fact_orders_customer_unique_id ON warehouse.fact_orders (customer_unique_id);
CREATE INDEX ix_fact_orders_status              ON warehouse.fact_orders (order_status);
CREATE INDEX ix_fact_orders_purchase_date        ON warehouse.fact_orders (order_purchase_date_sk);
-- CREATE INDEX ix_fact_orders_is_late              ON warehouse.fact_orders (is_late) WHERE is_late = 1;
CREATE INDEX ix_fact_orders_is_late             ON warehouse.fact_orders (is_late);   -- fixed: standard index, no filter
GO

PRINT 'warehouse.fact_orders created.';
GO
