/* =============================================================================
   warehouse.dim_product
   Source: staging.stg_products LEFT JOIN staging.stg_product_category_translation
   1.85% of products have a NULL category (per Phase 2 EDA) — mapped to
   'UNKNOWN' here, not left NULL, per the locked EDA decision.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('warehouse.dim_product', 'U') IS NOT NULL
    DROP TABLE warehouse.dim_product;
GO

CREATE TABLE warehouse.dim_product (
    product_sk                     INT IDENTITY(1,1) NOT NULL,
    product_id                     VARCHAR(50)   NOT NULL,
    product_category_name          NVARCHAR(100) NULL,       -- original PT-BR, kept for traceability
    product_category_name_english  NVARCHAR(100) NOT NULL,   -- 'UNKNOWN' if untranslated/missing
    product_name_length            INT           NULL,        -- de-misspelled on the way into Silver
    product_description_length     INT           NULL,
    product_photos_qty             INT           NULL,
    product_weight_g               FLOAT         NULL,
    product_length_cm              FLOAT         NULL,
    product_height_cm              FLOAT         NULL,
    product_width_cm               FLOAT         NULL,
    product_volume_cm3             AS (
        CASE WHEN product_length_cm IS NOT NULL
                  AND product_height_cm IS NOT NULL
                  AND product_width_cm IS NOT NULL
             THEN product_length_cm * product_height_cm * product_width_cm
             ELSE NULL END
    ) PERSISTED,
    created_at                     DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_dim_product PRIMARY KEY (product_sk),
    CONSTRAINT uq_dim_product_id UNIQUE (product_id)
);
GO

CREATE INDEX ix_dim_product_category_en ON warehouse.dim_product (product_category_name_english);
GO

PRINT 'warehouse.dim_product created.';
GO
