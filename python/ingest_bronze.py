"""
Olist Bronze Layer Ingestion Pipeline
======================================
CRM Customer Intelligence Module — Medallion Lakehouse (SQL Server)

Loads 8 raw CSV files from the Brazilian E-Commerce (Olist) dataset into
the `staging` schema (Bronze layer): untouched, append-only, audited.

Design notes (read before modifying):
- Per-file dtype maps, not one global dict. A column name colliding across
  two files (e.g. `order_id`) must not silently inherit the wrong type.
- One `batch_id` / `load_timestamp` per pipeline RUN, not per chunk. All
  rows loaded in the same execution carry identical audit values so you
  can reconcile a load by batch_id later.
- No FK validation here. Staging is raw-and-untransformed by definition;
  referential integrity is enforced and checked in the Silver layer
  (warehouse schema), against `dim_customer` / `fact_orders`, not here.
- Schema/table existence is checked, not created. DDL lives in
  sql/00_setup and sql/01_staging and is expected to have already run.
- `to_sql` chunk sizing is computed from column count to stay under
  SQL Server's 2100-parameter-per-statement ceiling, not hardcoded.

Usage:
    python ingest_bronze.py                  # full run, all 8 files
    python ingest_bronze.py --table stg_orders
    python ingest_bronze.py --force          # reload even if row counts match
    python ingest_bronze.py --dry-run         # validate files/config, load nothing

Author: Abdallah
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, DBAPIError

try:
    from config import CONNECTION_STRING
except ImportError:
    raise ImportError(
        "config.py not found next to this script. Create it with a "
        "CONNECTION_STRING variable, e.g.:\n"
        "  CONNECTION_STRING = "
        "'mssql+pyodbc:///?odbc_connect=DRIVER%3D...'"
    )

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = r"C:\Users\User\Desktop\crm-customer-intelligence-module\data\raw\Brazilian E-Commerce Public Dataset by Olist"
LOG_DIR = os.path.join(BASE_DIR, "logs")
MANIFEST_DIR = os.path.join(BASE_DIR, "logs", "manifests")
SCHEMA = "staging"

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(MANIFEST_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------


def setup_logging(run_id: str) -> logging.Logger:
    log_file = os.path.join(LOG_DIR, f"ingestion_{run_id}.log")
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt))
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(fmt))

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    return logging.getLogger("bronze_ingest")


# -----------------------------------------------------------------------------
# Per-file configuration: dtypes, date columns, dependencies, sizing
# -----------------------------------------------------------------------------
# NOTE: dtypes are scoped per file. A column name reused across files
# (order_id, product_id, seller_id) is declared independently in each
# file's own dict so a future file can't silently borrow the wrong type.


@dataclass
class FileSpec:
    table: str
    filename: str
    dtypes: Dict[str, type]
    date_cols: List[str] = field(default_factory=list)
    expected_rows: Optional[int] = None
    chunksize: int = 20_000          # rows read from CSV per chunk
    write_chunksize: Optional[int] = None  # rows written to SQL per batch; computed if None
    depends_on: List[str] = field(default_factory=list)


FILES: Dict[str, FileSpec] = {
    "stg_customers": FileSpec(
        table="stg_customers",
        filename="olist_customers_dataset.csv",
        dtypes={
            "customer_id": str,
            "customer_unique_id": str,
            "customer_zip_code_prefix": str,  # preserve leading zeros
            "customer_city": str,
            "customer_state": str,
        },
        expected_rows=99_441,
    ),
    "stg_sellers": FileSpec(
        table="stg_sellers",
        filename="olist_sellers_dataset.csv",
        dtypes={
            "seller_id": str,
            "seller_zip_code_prefix": str,
            "seller_city": str,
            "seller_state": str,
        },
        expected_rows=3_095,
    ),
    "stg_products": FileSpec(
        table="stg_products",
        filename="olist_products_dataset.csv",
        dtypes={
            "product_id": str,
            "product_category_name": str,
            "product_name_lenght": float,    # source CSV misspells "length" — kept as-is
            "product_description_lenght": float,
            "product_photos_qty": float,
            "product_weight_g": float,
            "product_length_cm": float,
            "product_height_cm": float,
            "product_width_cm": float,
        },
        expected_rows=32_951,
    ),
    "stg_geolocation": FileSpec(
        table="stg_geolocation",
        filename="olist_geolocation_dataset.csv",
        dtypes={
            "geolocation_zip_code_prefix": str,
            "geolocation_lat": float,
            "geolocation_lng": float,
            "geolocation_city": str,
            "geolocation_state": str,
        },
        expected_rows=1_000_163,  # post-dedup approx; raw file has duplicate zip/lat/lng rows — see note below
        chunksize=100_000,
    ),
    "stg_orders": FileSpec(
        table="stg_orders",
        filename="olist_orders_dataset.csv",
        dtypes={
            "order_id": str,
            "customer_id": str,
            "order_status": str,
        },
        date_cols=[
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
        expected_rows=99_441,
        write_chunksize=50,
        depends_on=["stg_customers"],
    ),
    "stg_order_items": FileSpec(
        table="stg_order_items",
        filename="olist_order_items_dataset.csv",
        dtypes={
            "order_id": str,
            "order_item_id": int,
            "product_id": str,
            "seller_id": str,
            "price": float,
            "freight_value": float,
        },
        date_cols=["shipping_limit_date"],
        expected_rows=112_650,
        write_chunksize=50,
        depends_on=["stg_orders", "stg_products", "stg_sellers"],
    ),
    "stg_order_payments": FileSpec(
        table="stg_order_payments",
        filename="olist_order_payments_dataset.csv",
        dtypes={
            "order_id": str,
            "payment_sequential": int,
            "payment_type": str,
            "payment_installments": int,
            "payment_value": float,
        },
        expected_rows=103_886,
        write_chunksize=50,
        depends_on=["stg_orders"],
    ),
    "stg_order_reviews": FileSpec(
        table="stg_order_reviews",
        filename="olist_order_reviews_dataset.csv",
        dtypes={
            "review_id": str,
            "order_id": str,
            "review_score": float,
            "review_comment_title": str,
            "review_comment_message": str,
        },
        date_cols=["review_creation_date", "review_answer_timestamp"],
        expected_rows=99_224,
        write_chunksize=50,
        depends_on=["stg_orders"],
    ),
    "stg_product_category_translation": FileSpec(
        table="stg_product_category_translation",
        filename="product_category_name_translation.csv",
        dtypes={
            "product_category_name": str,
            "product_category_name_english": str,
        },
        expected_rows=71,
        write_chunksize=50,
        depends_on=[],
    ),
}

# Topological load order — dependencies are informational at Bronze (no FK
# enforcement happens here), but loading in this order keeps logs readable
# and mirrors the order Silver will need them in.
LOAD_ORDER: List[str] = [
    "stg_customers",
    "stg_sellers",
    "stg_products",
    "stg_product_category_translation",
    "stg_geolocation",
    "stg_orders",
    "stg_order_items",
    "stg_order_payments",
    "stg_order_reviews",
]

NA_VALUES = ["", "NULL", "null", "None", "NaN", "nan"]

# SQL Server caps a single statement at 2100 bound parameters. pandas'
# to_sql(method="multi") packs `chunksize * ncols` params per INSERT, so
# the write chunksize must be derived from column count, not hardcoded.
SQLSERVER_PARAM_LIMIT = 2100
SAFE_PARAM_MARGIN = 0.9  # stay under the ceiling, not flush against it


def compute_write_chunksize(n_cols: int, requested: Optional[int]) -> int:
    """Largest row-batch size that stays under SQL Server's param limit."""
    max_rows = int((SQLSERVER_PARAM_LIMIT * SAFE_PARAM_MARGIN) // max(n_cols, 1))
    max_rows = max(max_rows, 50)  # never go absurdly small
    if requested is None:
        return max_rows
    return min(requested, max_rows)


# -----------------------------------------------------------------------------
# Database engine
# -----------------------------------------------------------------------------


@contextmanager
def get_engine():
    engine = create_engine(
        CONNECTION_STRING,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,   # drops dead connections instead of erroring on use
        fast_executemany=True,
        echo=False,
    )
    try:
        yield engine
    finally:
        engine.dispose()


def with_retry(fn: Callable, attempts: int = 3, base_delay: float = 2.0, logger: Optional[logging.Logger] = None):
    """Retry transient DB operations (dropped connections, timeouts)."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (OperationalError, DBAPIError) as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            if logger:
                logger.warning(
                    f"Transient DB error (attempt {attempt}/{attempts}): {exc}. "
                    f"Retrying in {delay:.0f}s..."
                )
            time.sleep(delay)
    raise last_exc


def table_exists(engine: Engine, table_name: str, schema: str = SCHEMA) -> bool:
    inspector = inspect(engine)
    return table_name in inspector.get_table_names(schema=schema)


def get_row_count(engine: Engine, table_name: str, schema: str = SCHEMA) -> int:
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM [{schema}].[{table_name}]"))
        return int(result.scalar())


# -----------------------------------------------------------------------------
# Manifest — one JSON-ish record per run, per table, for auditability
# -----------------------------------------------------------------------------


@dataclass
class LoadResult:
    table: str
    status: str            # "loaded" | "skipped" | "failed"
    rows_loaded: int
    expected_rows: Optional[int]
    elapsed_seconds: float
    error: Optional[str] = None


def write_manifest(run_id: str, batch_id: str, results: List[LoadResult]) -> str:
    import json

    manifest_path = os.path.join(MANIFEST_DIR, f"manifest_{run_id}.json")
    payload = {
        "run_id": run_id,
        "batch_id": batch_id,
        "run_started_utc": run_id,
        "results": [
            {
                "table": r.table,
                "status": r.status,
                "rows_loaded": r.rows_loaded,
                "expected_rows": r.expected_rows,
                "elapsed_seconds": round(r.elapsed_seconds, 2),
                "error": r.error,
            }
            for r in results
        ],
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return manifest_path


# -----------------------------------------------------------------------------
# Core load logic
# -----------------------------------------------------------------------------


def _apply_date_cols(df: pd.DataFrame, date_cols: List[str]) -> None:
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")


def load_file(
    spec: FileSpec,
    engine: Engine,
    batch_id: str,
    batch_timestamp: datetime,
    logger: logging.Logger,
    dry_run: bool = False,
) -> LoadResult:
    filepath = os.path.join(DATA_DIR, spec.filename)
    start = time.time()

    if not os.path.exists(filepath):
        msg = f"File not found: {filepath}"
        logger.error(msg)
        return LoadResult(spec.table, "failed", 0, spec.expected_rows, time.time() - start, msg)

    if not table_exists(engine, spec.table):
        msg = (
            f"staging.{spec.table} does not exist. Run sql/00_setup and "
            f"sql/01_staging DDL before ingestion."
        )
        logger.error(msg)
        return LoadResult(spec.table, "failed", 0, spec.expected_rows, time.time() - start, msg)

    n_cols = len(spec.dtypes) + 2  # +2 for the audit columns we append
    write_chunksize = compute_write_chunksize(n_cols, spec.write_chunksize)

    if dry_run:
        logger.info(f"[DRY RUN] Would load {spec.filename} → staging.{spec.table} "
                     f"(write_chunksize={write_chunksize})")
        return LoadResult(spec.table, "skipped", 0, spec.expected_rows, time.time() - start, None)

    try:
        logger.info(f"Loading {spec.filename} → staging.{spec.table}")

        def _truncate():
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE [{SCHEMA}].[{spec.table}]"))

        with_retry(_truncate, logger=logger)

        total_rows = 0
        reader = pd.read_csv(
            filepath,
            encoding="utf-8",
            low_memory=False,
            dtype=spec.dtypes,
            chunksize=spec.chunksize,
            na_values=NA_VALUES,
            keep_default_na=True,
        )

        for chunk_num, chunk in enumerate(reader, start=1):
            _apply_date_cols(chunk, spec.date_cols)

            # Audit columns — identical across the whole run, not per-chunk
            chunk["load_timestamp"] = batch_timestamp
            chunk["source_file"] = spec.filename

            def _write(c=chunk):
                c.to_sql(
                    name=spec.table,
                    schema=SCHEMA,
                    con=engine,
                    if_exists="append",
                    index=False,
                    chunksize=write_chunksize,
                    method="multi",
                )

            with_retry(_write, logger=logger)

            total_rows += len(chunk)
            if chunk_num == 1 or chunk_num % 10 == 0:
                logger.info(f"  staging.{spec.table}: {total_rows:,} rows written...")

        elapsed = time.time() - start
        logger.info(f"Loaded {total_rows:,} rows into staging.{spec.table} in {elapsed:.1f}s")

        if spec.expected_rows and total_rows != spec.expected_rows:
            logger.warning(
                f"staging.{spec.table}: row count {total_rows:,} != expected "
                f"{spec.expected_rows:,}. Confirm this is expected "
                f"(e.g. dataset version or known dedup difference) before trusting Silver."
            )

        return LoadResult(spec.table, "loaded", total_rows, spec.expected_rows, elapsed, None)

    except Exception as exc:  # noqa: BLE001 — top-level boundary, must not crash the run
        elapsed = time.time() - start
        logger.error(f"Failed loading staging.{spec.table}: {exc}", exc_info=True)
        return LoadResult(spec.table, "failed", 0, spec.expected_rows, elapsed, str(exc))


def should_skip(engine: Engine, spec: FileSpec, force: bool, logger: logging.Logger) -> bool:
    if force:
        return False
    if not table_exists(engine, spec.table):
        return False
    existing = get_row_count(engine, spec.table)
    if spec.expected_rows and existing == spec.expected_rows:
        logger.info(f"Skipping staging.{spec.table} — already loaded ({existing:,} rows). Use --force to reload.")
        return True
    return False


# -----------------------------------------------------------------------------
# Pipeline entry point
# -----------------------------------------------------------------------------


def run_pipeline(
    target_table: Optional[str],
    force: bool,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_id = str(uuid.uuid4())
    batch_timestamp = datetime.now(timezone.utc)

    logger.info("=" * 70)
    logger.info("Olist Bronze Ingestion — run_id=%s batch_id=%s", run_id, batch_id)
    logger.info("Data directory: %s", DATA_DIR)
    logger.info("Mode: %s", "DRY RUN" if dry_run else ("FORCE RELOAD" if force else "normal"))
    logger.info("=" * 70)

    if not os.path.isdir(DATA_DIR):
        logger.error("Data directory not found: %s", DATA_DIR)
        logger.error("Place the 8 Olist CSV files under data/raw/ first.")
        return False

    tables_to_load = [target_table] if target_table else LOAD_ORDER
    unknown = [t for t in tables_to_load if t not in FILES]
    if unknown:
        logger.error("Unknown table(s) requested: %s. Valid: %s", unknown, list(FILES))
        return False

    results: List[LoadResult] = []

    with get_engine() as engine:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection OK")
        except Exception as exc:
            logger.error("Database connection failed: %s", exc)
            return False

        for table in tables_to_load:
            spec = FILES[table]
            logger.info("-" * 60)
            logger.info("Table: %s  (file: %s)", spec.table, spec.filename)
            if spec.depends_on:
                logger.info("  Declared dependency on (informational only): %s", spec.depends_on)

            if should_skip(engine, spec, force, logger):
                results.append(LoadResult(spec.table, "skipped", get_row_count(engine, spec.table), spec.expected_rows, 0.0))
                continue

            result = load_file(spec, engine, batch_id, batch_timestamp, logger, dry_run=dry_run)
            results.append(result)

    manifest_path = write_manifest(run_id, batch_id, results)
    logger.info("=" * 70)
    logger.info("Manifest written: %s", manifest_path)

    loaded = sum(1 for r in results if r.status == "loaded")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")

    logger.info("Summary: %d loaded, %d skipped, %d failed (of %d requested)",
                loaded, skipped, failed, len(results))

    if failed:
        for r in results:
            if r.status == "failed":
                logger.error("  FAILED: %s — %s", r.table, r.error)
        return False

    logger.info("Bronze layer ingestion complete.")
    logger.info("Next: run sql/02_warehouse DDL, then build Silver "
                "(dim_customer SCD2, fact_orders, dim_review, dim_date).")
    return True


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Olist Bronze Layer Ingestion Pipeline")
    parser.add_argument("--table", type=str, default=None,
                         help="Load a single table only (e.g. stg_orders). Default: all 8.")
    parser.add_argument("--force", action="store_true",
                         help="Reload even if the table already has the expected row count.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Validate files/config and log intended actions without writing.")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger = setup_logging(run_id)

    ok = run_pipeline(
        target_table=args.table,
        force=args.force,
        dry_run=args.dry_run,
        logger=logger,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
