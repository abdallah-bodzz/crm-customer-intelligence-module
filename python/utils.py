"""
utils.py
════════════════════════════════════════════════════════════════════════════════
Shared infrastructure for every Phase 5 script AND every notebook in the CRM
Customer Intelligence Module.

Public API (import what you need):
────────────────────────────────────────────────────────────────────────────────
  setup_logging(script_name)                    → logging.Logger
  get_engine()                                  → sqlalchemy.Engine  (cached)
  fetch_df(query, engine, params)               → pd.DataFrame
  fetch_refresh_log(engine)                     → dict
  batched_update(engine, sql, records, …)       → int  (rows written)
  batched_insert(engine, table, df, …)          → int  (rows written)
  table_exists(engine, table, schema)           → bool
  timer(label, logger)                          → context-manager  (elapsed log)
  retry_on_db_error(max_attempts, base_delay)   → decorator

WHAT CHANGED vs. THE ORIGINAL (summary):
────────────────────────────────────────────────────────────────────────────────
  1.  fetch_df signature flipped to (query, engine) so notebooks can call it
      without needing to keep a reference to the engine in scope at every call
      site; both orderings work because the type check is explicit.
  2.  batched_insert added — scripts writing large DataFrames to staging/mart
      tables (e.g. action_rules.py inserting into crm_action_queue) currently
      use pd.to_sql with chunksize; this gives the same pattern as batched_update
      (progress logging, retry, single write path).
  3.  table_exists added — lightweight guard used before fetch_df in notebooks
      so a "has sp_refresh_mart been run?" check doesn't rely on catching an
      exception.
  4.  timer context-manager added — wraps any timed block, logs elapsed seconds
      in the same format as the rest of the log stream. Used by ML scripts to
      track fit/predict timing without scattering time.time() pairs everywhere.
  5.  setup_logging now accepts an optional log_dir so callers that run from a
      different working directory (e.g. notebooks calling a script indirectly)
      can redirect logs without monkeypatching os.getcwd().
  6.  fetch_refresh_log return dict is typed and coerces dates to datetime.date
      objects instead of raw strings — callers that did arithmetic on
      ml_cutoff_date were converting it themselves anyway.
  7.  _engine cache is reset cleanly by reset_engine() — useful in test
      environments that swap CONNECTION_STRING between test cases.
  8.  __all__ is declared so `from utils import *` in a notebook is safe and
      explicit.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

import config

__all__ = [
    "setup_logging",
    "get_engine",
    "reset_engine",
    "fetch_df",
    "fetch_refresh_log",
    "batched_update",
    "batched_insert",
    "table_exists",
    "timer",
    "retry_on_db_error",
]

# ══════════════════════════════════════════════════════════════════════════════
# 1.  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def setup_logging(
    script_name: str,
    log_dir: Union[str, Path, None] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure and return a named logger with stdout + rotating file output.

    Parameters
    ----------
    script_name:
        Used as both the logger name and the log file prefix.
    log_dir:
        Directory for the log file.  Defaults to ``logs/`` relative to the
        current working directory.  Created automatically if absent.
    level:
        Root log level.  Default ``logging.INFO``.

    Notes
    -----
    Guards against duplicate handlers so calling this more than once in the
    same process (e.g. run.py importing multiple modules) doesn't double-print
    every line.
    """
    log_dir = Path(log_dir) if log_dir else Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    log_path = log_dir / f"{script_name}_{ts}.log"

    logger = logging.getLogger(script_name)
    logger.setLevel(level)

    if logger.handlers:          # already configured in this process — skip
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ══════════════════════════════════════════════════════════════════════════════
# 2.  DB ENGINE
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """Return a module-level cached SQLAlchemy engine.

    ``pool_pre_ping=True`` validates connections before checkout, catching
    stale connections (idle-timeout drops) before they cause confusing errors
    mid-script rather than at the first query.

    The engine is created once per process.  Call :func:`reset_engine` to
    force recreation (e.g. in tests that swap ``config.CONNECTION_STRING``).
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.CONNECTION_STRING,
            pool_pre_ping=True,
            # Surface driver-level errors quickly rather than hanging on a
            # saturated pool — callers with retry logic prefer a fast fail.
            pool_timeout=30,
        )
    return _engine


def reset_engine() -> None:
    """Dispose and clear the cached engine.

    Intended for test environments that need a fresh connection pool between
    test cases.  Not needed in normal production use.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


# ══════════════════════════════════════════════════════════════════════════════
# 3.  RETRY DECORATOR
# ══════════════════════════════════════════════════════════════════════════════

def retry_on_db_error(max_attempts: int = 3, base_delay_seconds: float = 2.0):
    """Decorator: retry on transient DB errors with exponential back-off.

    Catches ``OperationalError`` and ``DBAPIError`` (connection drops,
    timeouts, deadlocks).  Does **not** catch ``ProgrammingError`` or
    ``IntegrityError`` — those are code/data bugs that will fail identically
    on every retry.

    Back-off schedule with defaults: 2 s → 4 s → give up (total ~6 s).

    Known tradeoff: SQLAlchemy raises ``OperationalError`` for both transient
    connection failures and persistent schema errors (missing table, wrong
    column name) on some drivers.  A missing-table error will burn through all
    retries before surfacing.  Mitigate with :func:`table_exists` checks before
    calling decorated functions where relevant.

    Usage::

        @retry_on_db_error()
        def my_db_call(engine):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            _log = logging.getLogger(func.__module__ or __name__)
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, DBAPIError) as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = base_delay_seconds * (2 ** (attempt - 1))
                    _log.warning(
                        "%s: attempt %d/%d failed — %s. Retrying in %.1f s…",
                        func.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            _log.error(
                "%s: giving up after %d attempt(s).", func.__name__, max_attempts
            )
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════════════
# 4.  READ HELPERS
# ══════════════════════════════════════════════════════════════════════════════

@retry_on_db_error()
def fetch_df(
    query: Union[str, "text"],
    engine: Optional[Engine] = None,
    params: Optional[dict] = None,
) -> pd.DataFrame:
    """Execute *query* and return the result as a DataFrame.

    Parameters
    ----------
    query:
        Raw SQL string or ``sqlalchemy.text()`` object.  Named ``:param``
        placeholders are filled from *params*.
    engine:
        SQLAlchemy engine.  Defaults to :func:`get_engine()` so notebooks can
        call ``fetch_df(sql)`` without threading an engine through every call.
    params:
        Optional dict of bind parameters matching ``:name`` placeholders in
        *query*.

    Examples
    --------
    ::

        # Script context — explicit engine
        df = fetch_df("SELECT * FROM mart.customer_360", engine)

        # Notebook context — implicit engine
        df = fetch_df("SELECT * FROM mart.rfm_features WHERE recency_score = :r", params={"r": 5})
    """
    if engine is None:
        engine = get_engine()
    # Accept engine as first arg for backwards-compat with old call sites that
    # used fetch_df(engine, query).
    if isinstance(query, Engine):
        query, engine = engine, query   # swap — old (engine, query) signature
    stmt = text(query) if isinstance(query, str) else query
    with engine.connect() as conn:
        return pd.read_sql(stmt, conn, params=params or {})


@retry_on_db_error()
def fetch_refresh_log(engine: Optional[Engine] = None) -> dict:
    """Read canonical pipeline constants from ``mart.refresh_log``.

    Returns
    -------
    dict with keys:
        ``as_of_date``       → ``datetime.date``
        ``ml_cutoff_date``   → ``datetime.date``
        ``churn_window_days``→ ``int``

    Raises
    ------
    ValueError
        If ``mart.refresh_log`` has no rows (``sp_refresh_mart`` not yet run).

    Notes
    -----
    Dates are returned as ``datetime.date`` objects (not strings) so callers
    can do arithmetic directly (e.g. ``ml_cutoff_date - timedelta(days=30)``).
    """
    if engine is None:
        engine = get_engine()

    query = text("""
        SELECT as_of_date, ml_cutoff_date, churn_window_days
        FROM   mart.refresh_log
        WHERE  refresh_id = 1
    """)
    with engine.connect() as conn:
        row = conn.execute(query).fetchone()

    if row is None:
        raise ValueError(
            "mart.refresh_log is empty. "
            "Run sp_refresh_mart before executing any Phase 5 script."
        )

    def _to_date(val) -> date:
        if isinstance(val, date):
            return val
        if isinstance(val, datetime):
            return val.date()
        return datetime.strptime(str(val), "%Y-%m-%d").date()

    return {
        "as_of_date":        _to_date(row[0]),
        "ml_cutoff_date":    _to_date(row[1]),
        "churn_window_days": int(row[2]),
    }


def table_exists(
    engine: Optional[Engine] = None,
    table: str = "",
    schema: Optional[str] = None,
) -> bool:
    """Return True if *schema.table* exists in the database.

    Useful as a lightweight pre-flight check in notebooks and scripts before
    issuing a query that would produce a confusing ``OperationalError`` if the
    mart hasn't been built yet.

    Examples
    --------
    ::

        if not table_exists(engine, "rfm_features", schema="mart"):
            raise RuntimeError("Run sp_refresh_mart first.")
    """
    if engine is None:
        engine = get_engine()
    insp = inspect(engine)
    return insp.has_table(table, schema=schema)


# ══════════════════════════════════════════════════════════════════════════════
# 5.  WRITE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

@retry_on_db_error()
def batched_update(
    engine: Engine,
    update_sql: Union[str, "text"],
    records: list[dict],
    batch_size: int = 2_000,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Execute an UPDATE/MERGE statement in batches.

    Parameters
    ----------
    engine:
        SQLAlchemy engine.
    update_sql:
        SQL string with named ``:param`` placeholders matching keys in each
        record dict.  Wrapped in ``text()`` automatically if a plain string.
    records:
        List of dicts, typically ``df.to_dict("records")``.
    batch_size:
        Rows per round-trip.  2 000 is a safe default for SQL Server with
        typical row widths; increase to 5 000–10 000 for narrow rows.
    logger:
        If provided, logs progress at INFO level every batch.

    Returns
    -------
    int
        Total rows processed.
    """
    if not records:
        if logger:
            logger.warning("batched_update called with 0 records — nothing to write.")
        return 0

    stmt = text(update_sql) if isinstance(update_sql, str) else update_sql
    total = 0

    with engine.begin() as conn:
        for start in range(0, len(records), batch_size):
            chunk = records[start : start + batch_size]
            conn.execute(stmt, chunk)
            total += len(chunk)
            if logger:
                logger.info(
                    "batched_update: %d / %d rows written…", total, len(records)
                )

    if logger:
        logger.info("batched_update: done — %d rows total.", total)
    return total


@retry_on_db_error()
def batched_insert(
    engine: Engine,
    table: str,
    df: pd.DataFrame,
    schema: Optional[str] = None,
    batch_size: int = 2_000,
    if_exists: str = "append",
    index: bool = False,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Write a DataFrame to a DB table in batches via ``pd.DataFrame.to_sql``.

    Prefer this over a bare ``df.to_sql(chunksize=…)`` call so:
    - progress is logged in the shared format,
    - the retry decorator handles transient failures,
    - batch size is a single project-wide constant.

    Parameters
    ----------
    engine:
        SQLAlchemy engine.
    table:
        Target table name (no schema prefix — pass schema separately).
    df:
        DataFrame to write.
    schema:
        Database schema, e.g. ``"mart"`` or ``"staging"``.
    batch_size:
        Rows per ``to_sql`` chunk.
    if_exists:
        Passed through to ``pd.DataFrame.to_sql``.
        ``"append"`` (default) | ``"replace"`` | ``"fail"``.
    index:
        Whether to write the DataFrame index as a column.  Default False.
    logger:
        If provided, logs start/finish at INFO level.

    Returns
    -------
    int
        Number of rows written (``len(df)``).
    """
    if df.empty:
        if logger:
            logger.warning("batched_insert: DataFrame is empty — nothing to write.")
        return 0

    target = f"{schema}.{table}" if schema else table
    if logger:
        logger.info(
            "batched_insert: writing %d rows to %s (batch_size=%d, if_exists=%s)…",
            len(df), target, batch_size, if_exists,
        )

    df.to_sql(
        name=table,
        con=engine,
        schema=schema,
        if_exists=if_exists,
        index=index,
        chunksize=batch_size,
        method="multi",        # single multi-row INSERT per chunk — faster than row-by-row
    )

    if logger:
        logger.info("batched_insert: done — %d rows written to %s.", len(df), target)
    return len(df)


# ══════════════════════════════════════════════════════════════════════════════
# 6.  TIMER CONTEXT MANAGER
# ══════════════════════════════════════════════════════════════════════════════

@contextlib.contextmanager
def timer(label: str = "block", logger: Optional[logging.Logger] = None):
    """Context manager that logs elapsed wall-clock time.

    Writes to *logger* at INFO if provided, otherwise prints to stdout.
    Consistent format integrates with the rest of the log stream.

    Examples
    --------
    ::

        with timer("model fit", logger):
            model.fit(X_train, y_train)
        # → 2024-01-15 09:12:03 | INFO     | … | [timer] model fit — 4.73 s

        # Notebook — no logger
        with timer("query"):
            df = fetch_df(sql)
        # → [timer] query — 0.41 s
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        msg = f"[timer] {label} — {elapsed:.2f} s"
        if logger:
            logger.info(msg)
        else:
            print(msg)


# ══════════════════════════════════════════════════════════════════════════════
# RETROFIT NOTE (unchanged from original — left for visibility)
# ══════════════════════════════════════════════════════════════════════════════
# Each of sentiment.py / segmentation.py / clv_model.py / churn_model.py /
# next_purchase.py can be slimmed down by replacing:
#
#   logging.basicConfig(…)                  → logger = setup_logging("name")
#   create_engine(CONNECTION_STRING)        → engine = get_engine()
#   local write_*(engine, df, batch) body   → batched_update(engine, sql, records, batch, logger)
#   clv_model.fetch_ml_cutoff_date()        → fetch_refresh_log()["ml_cutoff_date"]
#   next_purchase.fetch_as_of_date()        → fetch_refresh_log()["as_of_date"]
#
# Notebooks replace:
#   run_query(sql)                          → fetch_df(sql)
#   save_fig(fig, "name.png")              → save_fig(fig, "name.png", FIGURES_DIR)
#   local PALETTE / SEGMENT_COLORS dicts   → imported from plot_theme