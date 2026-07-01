/* =============================================================================
   warehouse.dim_date — calendar dimension
   Generated, not hand-typed. Covers 2015-01-01 .. 2020-12-31 (Olist range
   is 2016-09 .. 2018-10; buffer added both sides for safety).
   Run once. Re-running drops and rebuilds (idempotent, ~2k rows, instant).
   ============================================================================= */
USE CRM_Analytics;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'warehouse')
    EXEC('CREATE SCHEMA warehouse');
GO

IF OBJECT_ID('warehouse.dim_date', 'U') IS NOT NULL
    DROP TABLE warehouse.dim_date;
GO

CREATE TABLE warehouse.dim_date (
    date_sk              INT          NOT NULL,   -- YYYYMMDD
    full_date            DATE         NOT NULL,
    year                 SMALLINT     NOT NULL,
    quarter              TINYINT      NOT NULL,
    month                TINYINT      NOT NULL,
    month_name           NVARCHAR(12) NOT NULL,
    week_of_year         TINYINT      NOT NULL,
    day_of_month         TINYINT      NOT NULL,
    day_name             NVARCHAR(12) NOT NULL,
    is_weekend           BIT          NOT NULL,
    is_brazilian_holiday BIT          NOT NULL DEFAULT 0,
    holiday_name         NVARCHAR(100) NULL,
    fiscal_year          SMALLINT     NOT NULL,   -- Olist runs calendar-year fiscal; kept as a
    fiscal_quarter       TINYINT      NOT NULL,   -- standard dim attribute for portability/signal

    CONSTRAINT pk_dim_date PRIMARY KEY (date_sk)
);
GO

DECLARE @start DATE = '2015-01-01';
DECLARE @end   DATE = '2020-12-31';
DECLARE @d     DATE = @start;

WHILE @d <= @end
BEGIN
    INSERT INTO warehouse.dim_date (
        date_sk, full_date, year, quarter, month, month_name,
        week_of_year, day_of_month, day_name, is_weekend,
        fiscal_year, fiscal_quarter
    )
    VALUES (
        CONVERT(INT, CONVERT(NVARCHAR(8), @d, 112)),
        @d, YEAR(@d), DATEPART(QUARTER, @d), MONTH(@d), DATENAME(MONTH, @d),
        DATEPART(ISO_WEEK, @d), DAY(@d), DATENAME(WEEKDAY, @d),
        CASE WHEN DATEPART(WEEKDAY, @d) IN (1,7) THEN 1 ELSE 0 END,
        YEAR(@d), DATEPART(QUARTER, @d)
    );
    SET @d = DATEADD(DAY, 1, @d);
END;
GO

/* Unknown-date member. Kimball rule: a fact's date FK should never be NULL —
   route unresolvable dates here instead of leaving a NULL join key that
   silently drops rows out of every Power BI date-axis visual. */
IF NOT EXISTS (SELECT 1 FROM warehouse.dim_date WHERE date_sk = 19000101)
BEGIN
    INSERT INTO warehouse.dim_date (
        date_sk, full_date, year, quarter, month, month_name,
        week_of_year, day_of_month, day_name, is_weekend,
        is_brazilian_holiday, holiday_name, fiscal_year, fiscal_quarter
    )
    VALUES (
        19000101, '1900-01-01', 1900, 1, 1, 'Unknown',
        1, 1, 'Unknown', 0,
        0, 'UNKNOWN_DATE', 1900, 1
    );
END
GO

/* Fixed-date Brazilian national holidays, 2015-2020.
   Movable feasts (Carnaval, Good Friday, Corpus Christi) intentionally
   omitted — they require a date-of-Easter calc, not worth the complexity
   for a portfolio project. Documented here as a known gap, not an oversight. */
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'New Year''s Day'        WHERE month = 1  AND day_of_month = 1;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'Tiradentes'              WHERE month = 4  AND day_of_month = 21;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'Labour Day'               WHERE month = 5  AND day_of_month = 1;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'Independence Day'         WHERE month = 9  AND day_of_month = 7;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'Nossa Sra. Aparecida'      WHERE month = 10 AND day_of_month = 12;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'All Souls'' Day'           WHERE month = 11 AND day_of_month = 2;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'Republic Day'              WHERE month = 11 AND day_of_month = 15;
UPDATE warehouse.dim_date SET is_brazilian_holiday = 1, holiday_name = 'Christmas Day'             WHERE month = 12 AND day_of_month = 25;
GO

CREATE INDEX ix_dim_date_full_date ON warehouse.dim_date (full_date);
GO

PRINT 'warehouse.dim_date created and populated.';
GO
