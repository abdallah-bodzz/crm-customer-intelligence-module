/* =============================================================================
   warehouse.dim_seller
   Source: staging.stg_sellers
   No SCD2 here — seller location changes aren't a tracked business event
   for this project's scope (no historical seller-territory analysis planned).
   Type 1 (overwrite) is the right call; don't over-engineer.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('warehouse.dim_seller', 'U') IS NOT NULL
    DROP TABLE warehouse.dim_seller;
GO

CREATE TABLE warehouse.dim_seller (
    seller_sk               INT IDENTITY(1,1) NOT NULL,
    seller_id                VARCHAR(50)   NOT NULL,
    seller_zip_code_prefix   VARCHAR(10)   NULL,
    seller_city              NVARCHAR(150) NULL,
    seller_state             VARCHAR(5)    NULL,
    created_at                DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_dim_seller PRIMARY KEY (seller_sk),
    CONSTRAINT uq_dim_seller_id UNIQUE (seller_id)
);
GO

CREATE INDEX ix_dim_seller_state ON warehouse.dim_seller (seller_state);
GO

PRINT 'warehouse.dim_seller created.';
GO
