/* =============================================================================
   warehouse.dim_customer — SCD Type 2 on customer_unique_id
   Source: staging.stg_customers
   Business key: customer_unique_id (the real person — Olist issues a new
   customer_id per order, so customer_id is NOT unique per human).
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('warehouse.dim_customer', 'U') IS NOT NULL
    DROP TABLE warehouse.dim_customer;
GO

CREATE TABLE warehouse.dim_customer (
    customer_sk               INT IDENTITY(1,1) NOT NULL,

    customer_unique_id        VARCHAR(50)   NOT NULL,   -- business key
    customer_id               VARCHAR(50)   NOT NULL,   -- source operational key (N:1 -> unique_id)

    customer_zip_code_prefix  VARCHAR(10)   NULL,
    customer_city             NVARCHAR(150) NULL,
    customer_state            VARCHAR(5)    NULL,

    -- SCD2
    valid_from                DATE          NOT NULL,
    valid_to                  DATE          NULL,        -- NULL = currently active
    is_current                BIT           NOT NULL DEFAULT 1,

    created_at                DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at                 DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_dim_customer PRIMARY KEY (customer_sk)
);
GO

CREATE INDEX ix_dim_customer_unique_id   ON warehouse.dim_customer (customer_unique_id) INCLUDE (is_current);
CREATE INDEX ix_dim_customer_id          ON warehouse.dim_customer (customer_id);
CREATE INDEX ix_dim_customer_state       ON warehouse.dim_customer (customer_state);

-- Enforce "exactly one current row per business key" at the DB level,
-- not just in application logic — a filtered unique index is the cheapest
-- correctness guard SCD2 tables can have.
CREATE UNIQUE INDEX ux_dim_customer_current
    ON warehouse.dim_customer (customer_unique_id)
    WHERE is_current = 1;
GO

PRINT 'warehouse.dim_customer created.';
GO
