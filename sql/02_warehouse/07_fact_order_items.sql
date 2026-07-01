/* =============================================================================
   warehouse.fact_order_items
   Source: staging.stg_order_items, with FKs to dim_product / dim_seller and
   order_id linking back to fact_orders.
   Grain: one row per (order_id, order_item_id) — product-level line item.
   ============================================================================= */
USE CRM_Analytics;
GO

IF OBJECT_ID('warehouse.fact_order_items', 'U') IS NOT NULL
    DROP TABLE warehouse.fact_order_items;
GO

CREATE TABLE warehouse.fact_order_items (
    item_sk               INT IDENTITY(1,1) NOT NULL,

    order_id              VARCHAR(50)   NOT NULL,
    order_item_id         INT           NOT NULL,
    customer_unique_id    VARCHAR(50)   NOT NULL,   -- denormalized for fast customer-level GMV rollups

    product_sk            INT           NULL,
    seller_sk              INT           NULL,
    product_id             VARCHAR(50)   NULL,        -- natural key, kept alongside SK for direct joins
    seller_id              VARCHAR(50)   NULL,

    price                  DECIMAL(12,2) NOT NULL,
    freight_value           DECIMAL(12,2) NOT NULL,
    gmv                      AS (price + freight_value) PERSISTED,
    freight_ratio            AS (
        CASE WHEN (price + freight_value) > 0
             THEN CAST(freight_value / (price + freight_value) AS DECIMAL(6,4))
             ELSE 0 END
    ) PERSISTED,

    shipping_limit_date    DATETIME2(3)  NULL,
    created_at              DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT pk_fact_order_items PRIMARY KEY (item_sk),
    CONSTRAINT uq_fact_order_items UNIQUE (order_id, order_item_id),
    CONSTRAINT fk_fact_items_product FOREIGN KEY (product_sk) REFERENCES warehouse.dim_product (product_sk),
    CONSTRAINT fk_fact_items_seller  FOREIGN KEY (seller_sk)  REFERENCES warehouse.dim_seller (seller_sk)
);
GO

CREATE INDEX ix_fact_items_order_id  ON warehouse.fact_order_items (order_id);
CREATE INDEX ix_fact_items_customer  ON warehouse.fact_order_items (customer_unique_id);
CREATE INDEX ix_fact_items_product   ON warehouse.fact_order_items (product_sk);
CREATE INDEX ix_fact_items_seller    ON warehouse.fact_order_items (seller_sk);
GO

PRINT 'warehouse.fact_order_items created.';
GO
