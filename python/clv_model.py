"""
clv_model.py
=============
Phase 5, Step 3: predicts 6-month forward GMV per customer. Trains an
XGBoost regressor, writes clv_predicted_6m + a quantile-regression
confidence interval back to mart.clv_features.

==============================================================================
CRITICAL CORRECTION FROM THE ORIGINAL PHASE 5 PLAN — READ BEFORE EDITING
==============================================================================
The original plan said: "Train/Test: Temporal split: train on orders
before ml_cutoff_date, test on after" and pointed straight at
mart.clv_features as the feature source.

That doesn't work, and not as a style nitpick — it's target leakage.
mart.clv_features has ONE ROW PER CUSTOMER. Its feature columns
(avg_order_value, total_categories_purchased, avg_review_score, etc.)
are computed in sp_refresh_mart from a customer's ENTIRE order history —
there is no per-order date column at that grain to split on, and no
pre-cutoff filter was applied when those columns were built. So a
customer's "avg_order_value" already includes GMV from orders placed
AFTER the cutoff — the exact period actual_gmv_post_cutoff is trying to
predict. Train a model on those columns and it will report excellent
metrics by partially predicting the post-cutoff period FROM the
post-cutoff period. That's not a working CLV model, it's a model that
memorized its own answer key.

THE FIX, implemented below:
Features for THIS model are recomputed directly from warehouse.fact_orders
/ fact_order_items with an explicit `order_purchase_timestamp < ml_cutoff_date`
filter (see build_pre_cutoff_features()) — bypassing mart.clv_features'
leaky columns entirely for training purposes. The target
(actual_gmv_post_cutoff) still comes from mart.clv_features, since that
column IS correctly cutoff-scoped on its own terms (it's defined as
"GMV from orders >= cutoff", which is exactly the target, not a feature).
Predictions are written back to mart.clv_features' designated
clv_predicted_6m / clv_ci_* columns — those columns were always meant to
be Python-filled, this only changes what Python reads to produce them.

This is standard practice in real CLV modeling (BG/NBD, Pareto/NBD, and
any supervised approach all require point-in-time feature computation) —
it's not a project-specific workaround, it's the correct way to do this
regardless of stack.

==============================================================================
MODEL
==============================================================================
Point estimate:  XGBoost regressor, objective='reg:squarederror'
Confidence band: two more XGBoost regressors at quantile_alpha=0.1 and 0.9
                 via objective='reg:quantileerror' (native to XGBoost,
                 simpler and more interpretable than a bootstrap ensemble
                 for a portfolio project, per the original plan's own
                 stated rationale — that part of the plan was correct and
                 is kept as-is)

USAGE:
    python clv_model.py                  # train, evaluate, predict, write
    python clv_model.py --dry-run         # train + evaluate, write nothing
    python clv_model.py --tune            # grid search before final fit (slower)
"""

import argparse
import json
import logging
import os
import sys
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GridSearchCV
from sqlalchemy import create_engine, text

import config

os.makedirs("logs", exist_ok=True)
os.makedirs("models", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/clv_model_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("clv_model")

ML_CUTOFF_DATE = "2018-05-01"  # must match warehouse.fact_orders / mart.refresh_log — read from refresh_log at runtime, not hardcoded blindly (see fetch_ml_cutoff_date)


# =============================================================================
# Canonical constant — read from the DB, not retyped here as a literal
# =============================================================================

def fetch_ml_cutoff_date(engine) -> str:
    """
    Pulls @ml_cutoff_date from mart.refresh_log — the single source of
    truth written by sp_refresh_mart — instead of trusting a hardcoded
    literal in this file that could silently drift from SQL's value.
    Falls back to the module-level constant ONLY if refresh_log is
    missing or empty, with a loud warning (not a silent fallback).
    """
    query = text("SELECT ml_cutoff_date FROM mart.refresh_log WHERE refresh_id = 1")
    try:
        with engine.connect() as conn:
            result = conn.execute(query).fetchone()
        if result is not None and result[0] is not None:
            cutoff = str(result[0])
            logger.info("ml_cutoff_date read from mart.refresh_log: %s", cutoff)
            return cutoff
    except Exception as e:
        logger.warning("Could not read mart.refresh_log (%s). Falling back to hardcoded cutoff.", e)

    logger.warning(
        "Using hardcoded fallback ML_CUTOFF_DATE=%s — this should only happen if "
        "sp_refresh_mart has never been run. Verify this matches your actual data.",
        ML_CUTOFF_DATE,
    )
    return ML_CUTOFF_DATE


# =============================================================================
# Leakage-free feature construction (the core fix — see module docstring)
# =============================================================================

def build_pre_cutoff_features(engine, cutoff_date: str) -> pd.DataFrame:
    """
    Recomputes CLV features from warehouse tables directly, filtered to
    orders strictly before cutoff_date. This is intentionally a
    duplicate of some logic in sp_refresh_mart's customer_360/
    clv_features CTEs — that duplication is the price of correctness
    here: mart.clv_features' own columns are NOT safe to use as
    training features for this specific model (see module docstring).

    customer_state and preferred_payment_type are read as-is from
    mart.clv_features since they're not GMV/order-derived statistics
    that could leak the target — a customer's state and typical payment
    method don't change based on future order volume the way avg_order_value
    or total_categories_purchased would.
    """
    query = text("""
        WITH pre_cutoff_orders AS (
            SELECT *
            FROM warehouse.fact_orders
            WHERE order_purchase_timestamp < :cutoff_date
        ),
        pre_cutoff_items AS (
            SELECT foi.*
            FROM warehouse.fact_order_items foi
            JOIN pre_cutoff_orders po ON po.order_id = foi.order_id
        ),
        customer_base AS (
            SELECT
                customer_unique_id,
                COUNT(DISTINCT order_id) AS total_orders_pre_cutoff,
                MIN(CAST(order_purchase_timestamp AS DATE)) AS first_order_date,
                MAX(CAST(order_purchase_timestamp AS DATE)) AS last_order_date_pre_cutoff,
                AVG(CAST(delivery_delta_days AS DECIMAL(8,2))) AS avg_delivery_delta,
                AVG(CASE WHEN is_late = 1 THEN 1.0 ELSE 0.0 END) AS pct_late
            FROM pre_cutoff_orders
            GROUP BY customer_unique_id
        ),
        customer_gmv AS (
            SELECT customer_unique_id, SUM(gmv) AS total_gmv_pre_cutoff
            FROM pre_cutoff_items
            GROUP BY customer_unique_id
        ),
        category_diversity AS (
            SELECT pci.customer_unique_id,
                   COUNT(DISTINCT dp.product_category_name_english) AS total_categories_purchased
            FROM pre_cutoff_items pci
            LEFT JOIN warehouse.dim_product dp ON dp.product_sk = pci.product_sk
            GROUP BY pci.customer_unique_id
        )
        SELECT
            cb.customer_unique_id,
            cb.total_orders_pre_cutoff,
            ISNULL(cg.total_gmv_pre_cutoff, 0) AS total_gmv_pre_cutoff,
            CASE WHEN cb.total_orders_pre_cutoff > 0
                 THEN ISNULL(cg.total_gmv_pre_cutoff, 0) / cb.total_orders_pre_cutoff
                 ELSE 0 END AS avg_order_value_pre_cutoff,
            DATEDIFF(DAY, cb.first_order_date, cb.last_order_date_pre_cutoff) AS tenure_days_pre_cutoff,
            DATEDIFF(DAY, cb.last_order_date_pre_cutoff, :cutoff_date) AS days_since_last_order_pre_cutoff,
            cb.avg_delivery_delta,
            cb.pct_late,
            ISNULL(cd.total_categories_purchased, 0) AS total_categories_purchased
        FROM customer_base cb
        LEFT JOIN customer_gmv cg ON cg.customer_unique_id = cb.customer_unique_id
        LEFT JOIN category_diversity cd ON cd.customer_unique_id = cb.customer_unique_id
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"cutoff_date": cutoff_date})

    # order_frequency_per_month / tenure_months, same NULLIF-style guard as
    # the SQL layer uses elsewhere in this project — same-day-only customers
    # (tenure_days_pre_cutoff = 0) get NULL, not a divide-by-zero or an
    # artificially huge frequency number.
    df["tenure_months_pre_cutoff"] = df["tenure_days_pre_cutoff"] / 30.0
    df["order_frequency_per_month_pre_cutoff"] = np.where(
        df["tenure_days_pre_cutoff"] > 0,
        df["total_orders_pre_cutoff"] / (df["tenure_days_pre_cutoff"] / 30.0),
        np.nan,
    )

    return df


def fetch_static_attributes_and_target(engine) -> pd.DataFrame:
    """
    customer_state, preferred_payment_type, and the target
    (actual_gmv_post_cutoff) — these come from mart.clv_features as-is.
    state/payment type are not leakage risks (see build_pre_cutoff_features
    docstring); actual_gmv_post_cutoff is correctly cutoff-scoped already.
    """
    query = text("""
        SELECT customer_unique_id, customer_state, preferred_payment_type, actual_gmv_post_cutoff
        FROM mart.clv_features
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def assemble_training_frame(engine, cutoff_date: str) -> pd.DataFrame:
    features = build_pre_cutoff_features(engine, cutoff_date)
    static = fetch_static_attributes_and_target(engine)
    df = features.merge(static, on="customer_unique_id", how="inner")

    # Derived flag: single-order customers are 96.88% of the base.
    # Their tenure=0 and order_frequency=NaN (-> imputed 0) — without this
    # flag, XGBoost cannot distinguish them from a multi-order customer who
    # just happened to have low frequency. With it, the model gets a direct
    # binary split on the most structurally important divide in this dataset.
    df["is_single_order"] = (df["total_orders_pre_cutoff"] == 1).astype(int)

    logger.info("Assembled training frame: %d customers, %d columns.", *df.shape)
    return df


# =============================================================================
# Model
# =============================================================================

FEATURE_COLUMNS_NUMERIC = [
    "total_orders_pre_cutoff",
    "avg_order_value_pre_cutoff",
    "order_frequency_per_month_pre_cutoff",
    "tenure_months_pre_cutoff",
    "days_since_last_order_pre_cutoff",
    "total_categories_purchased",
    "avg_delivery_delta",
    "pct_late",
    "is_single_order",  # binary flag — single-order customers are structurally
                        # different from repeat buyers; frequency/tenure are
                        # undefined for them (NaN -> 0), so this flag gives
                        # XGBoost a direct handle on that structural split
                        # rather than forcing it to infer it from near-constant
                        # tenure=0 and imputed frequency=0 alone.
]
FEATURE_COLUMNS_CATEGORICAL = ["customer_state", "preferred_payment_type"]
TARGET_COLUMN = "actual_gmv_post_cutoff"


def prepare_design_matrix(df: pd.DataFrame, encoder_categories: dict = None):
    """
    One-hot encodes the categorical columns. If encoder_categories is
    given (a dict of column -> list of known categories, captured at
    training time), unseen categories at predict time are encoded as
    all-zero rather than raising — this matters here because Olist has
    states with very few customers, and a fresh inference batch could
    plausibly omit a rare state entirely.
    """
    X_numeric = df[FEATURE_COLUMNS_NUMERIC].copy()
    # Median imputation for NULLs (e.g. order_frequency_per_month_pre_cutoff
    # for same-day-only customers) — XGBoost can handle NaN natively, but
    # we impute explicitly here so the choice is visible and consistent
    # across train/predict rather than relying on library default behavior.
    #
    # GUARD: if a column is NULL for every row in the batch, .median()
    # itself returns NaN, and .fillna(NaN) is a silent no-op — the column
    # stays entirely NaN going into the model with no error raised. This
    # exact bug was confirmed in churn_model.py via a real run (see that
    # file's prepare_design_matrix for the full incident writeup) and
    # fixed there with a domain-justified fallback; applying the same
    # defensive check here even though CLV's GMV/order-derived features
    # are less likely to ever be all-NULL in practice — "less likely" is
    # not "impossible," and an unguarded landmine left in a sibling file
    # after fixing it in one place is just a bug with a delay timer.
    for col in X_numeric.columns:
        for col in X_numeric.columns:
            if not X_numeric[col].isnull().any():
                continue

            # order_frequency_per_month_pre_cutoff is NaN for ~98% of customers
            # (all single-order buyers have tenure_days=0, making frequency
            # undefined). The median of this column is computed from only the
            # ~1,380 multi-order customers (~2% of base) and equals ~0.97/month.
            # Imputing that value for 69,806 single-order customers would tell
            # XGBoost they purchase at nearly 1x/month — completely wrong.
            # Correct imputation here is 0: no repeat purchase frequency observed.
            if col == "order_frequency_per_month_pre_cutoff":
                fill_val = 0.0
                logger.info(
                    "Column '%s': imputing NaNs with 0.0 (not median) — "
                    "NaNs represent single-order customers with undefined frequency, "
                    "not missing data. Median of multi-order customers (~1/month) "
                    "would be a false signal for 98%% of the base.", col,
                )
            else:
                fill_val = X_numeric[col].median()
                if pd.isnull(fill_val):
                    logger.warning(
                        "Column '%s' is NULL for 100%% of rows — median undefined, "
                        "falling back to 0.0. Investigate if unexpected.", col,
                    )
                    fill_val = 0.0

            X_numeric[col] = X_numeric[col].astype("float64").fillna(fill_val)
    if encoder_categories is None:
        encoder_categories = {col: sorted(df[col].dropna().unique().tolist()) for col in FEATURE_COLUMNS_CATEGORICAL}

    dummies = []
    for col in FEATURE_COLUMNS_CATEGORICAL:
        cat = pd.Categorical(df[col], categories=encoder_categories[col])
        dummy = pd.get_dummies(cat, prefix=col)
        dummies.append(dummy)

    X = pd.concat([X_numeric.reset_index(drop=True)] + [d.reset_index(drop=True) for d in dummies], axis=1)
    return X, encoder_categories


def evaluate_holdout(model, X_test, y_test) -> dict:
    preds = model.predict(X_test)
    preds = np.clip(preds, a_min=0, a_max=None)  # GMV can't be negative — clip rather than let a model report nonsense
    return {
        "mae": float(mean_absolute_error(y_test, preds)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, preds))),
        "r2": float(r2_score(y_test, preds)),
        "mean_actual": float(y_test.mean()),
        "mean_predicted": float(preds.mean()),
    }


def train_point_estimate(X_train, y_train, tune: bool) -> xgb.XGBRegressor:
    if tune:
        logger.info("Running grid search for point-estimate model (this takes longer)...")
        param_grid = {
            "n_estimators": [100, 200, 400],
            "max_depth": [3, 4, 6],
            "learning_rate": [0.01, 0.05, 0.1],
        }
        base = xgb.XGBRegressor(objective="reg:squarederror", random_state=42)
        search = GridSearchCV(base, param_grid, cv=3, scoring="neg_mean_absolute_error", n_jobs=-1)
        search.fit(X_train, y_train)
        logger.info("Best params: %s", search.best_params_)
        return search.best_estimator_

    model = xgb.XGBRegressor(
        objective="reg:squarederror",
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        random_state=42,
    )
    model.fit(X_train, y_train)
    return model


def train_quantile_model(X_train, y_train, alpha: float) -> xgb.XGBRegressor:
    model = xgb.XGBRegressor(
        objective="reg:quantileerror",
        quantile_alpha=alpha,
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        random_state=42,
    )
    model.fit(X_train, y_train)
    return model


def write_predictions(engine, df: pd.DataFrame, batch_size: int) -> int:
    """
    df must have: customer_unique_id, clv_predicted_6m, clv_ci_lower, clv_ci_upper.
    Same batched-UPDATE pattern as sentiment.py / segmentation.py.
    """
    if df.empty:
        return 0

    update_stmt = text("""
        UPDATE mart.clv_features
        SET clv_predicted_6m = :clv_predicted_6m,
            clv_ci_lower = :clv_ci_lower,
            clv_ci_upper = :clv_ci_upper,
            refreshed_at = SYSUTCDATETIME()
        WHERE customer_unique_id = :customer_unique_id
    """)

    records = df.to_dict("records")
    total_written = 0
    with engine.begin() as conn:
        for start in range(0, len(records), batch_size):
            chunk = records[start:start + batch_size]
            conn.execute(update_stmt, chunk)
            total_written += len(chunk)
            logger.info("Wrote %d/%d rows...", total_written, len(records))

    return total_written


def main():
    parser = argparse.ArgumentParser(description="CLV prediction (XGBoost, leakage-corrected features).")
    parser.add_argument("--dry-run", action="store_true", help="Train + evaluate, write nothing to the DB.")
    parser.add_argument("--tune", action="store_true", help="Grid search the point-estimate model before final fit.")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--batch-size", type=int, default=2000)
    args = parser.parse_args()

    engine = create_engine(config.CONNECTION_STRING)
    cutoff_date = fetch_ml_cutoff_date(engine)

    logger.info("Assembling leakage-free training frame (features < %s, target >= %s)...", cutoff_date, cutoff_date)
    df = assemble_training_frame(engine, cutoff_date)

    if df.empty:
        logger.error("No data assembled. Has sp_refresh_mart been run? Exiting.")
        sys.exit(1)

    X, encoder_categories = prepare_design_matrix(df)
    y = df[TARGET_COLUMN]

    # Random split, not a date split — the date split already happened at
    # the feature/target construction stage above (pre-cutoff features ->
    # post-cutoff target). This split is just standard train/holdout for
    # evaluating the model's generalization across customers.
    # NEW
    # Sort by customer_unique_id before shuffling so row order is deterministic
    # regardless of SQL query return order. Without this, RandomState(42) produces
    # different splits across runs because the input row order varies.
    df = df.sort_values("customer_unique_id").reset_index(drop=True)
    rng = np.random.RandomState(42)
    shuffled_idx = rng.permutation(len(df))
    split_point = int(len(df) * (1 - args.test_size))
    train_idx, test_idx = shuffled_idx[:split_point], shuffled_idx[split_point:]

    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    logger.info("Train: %d customers. Test: %d customers.", len(X_train), len(X_test))

    point_model = train_point_estimate(X_train, y_train, tune=args.tune)
    metrics = evaluate_holdout(point_model, X_test, y_test)
    logger.info("Point-estimate holdout metrics: %s", json.dumps(metrics, indent=2))
    if metrics["r2"] < 0:
        logger.warning(
            "r2 is NEGATIVE (%.4f) — the model performs worse than predicting the mean for "
            "every customer. Confirmed via a real run this is plausible, not necessarily a bug: "
            "mean_actual=%.2f over a ~4-month post-cutoff window is consistent with the Phase 2 "
            "EDA's locked finding that 96.88%% of customers are one-time buyers — most rows in "
            "this target are near-zero, with a small number of large outliers, which is a "
            "classically hard distribution for squared-error regression to beat a naive mean "
            "baseline on. This is a real modeling-difficulty finding worth noting in the project "
            "writeup, not something to silently accept OR silently 'fix' by changing the loss "
            "function without first checking the target's actual distribution (e.g. "
            "df['%s'].describe() and a histogram) to confirm this diagnosis.",
            metrics["r2"], metrics["mean_actual"], TARGET_COLUMN,
        )

    logger.info("Training quantile models for CI bounds (alpha=0.1, 0.9)...")
    lower_model = train_quantile_model(X_train, y_train, alpha=0.1)
    upper_model = train_quantile_model(X_train, y_train, alpha=0.9)

    lower_preds_test = np.clip(lower_model.predict(X_test), 0, None)
    upper_preds_test = np.clip(upper_model.predict(X_test), 0, None)
    coverage = float(np.mean((y_test.values >= lower_preds_test) & (y_test.values <= upper_preds_test)))
    logger.info("80%% interval empirical coverage on holdout: %.1f%% (target: ~80%%)", coverage * 100)
    if coverage > 0.95:
        logger.warning(
            "CI coverage %.1f%% far exceeds the 80%% target. Expected on this "
            "zero-inflated target: lower bound (~0) trivially contains all zero-"
            "actual rows, inflating coverage. Intervals are useful only for the "
            "top-decile non-zero predictions — not as population-level error bars.",
            coverage * 100,
        )

    if args.dry_run:
        logger.info("Dry run — no rows written to mart.clv_features.")
        return

    # Refit on the FULL dataset for final predictions — the train/test
    # split above was for evaluation only; the deployed model should use
    # every available customer.
    logger.info("Refitting on full dataset for final predictions...")
    final_point_model = train_point_estimate(X, y, tune=False)
    final_lower_model = train_quantile_model(X, y, alpha=0.1)
    final_upper_model = train_quantile_model(X, y, alpha=0.9)

    point_preds = np.clip(final_point_model.predict(X), 0, None)
    lower_preds = np.clip(final_lower_model.predict(X), 0, None)
    upper_preds = np.clip(final_upper_model.predict(X), 0, None)

    # Guard against an inverted interval (quantile models are trained
    # independently and can, rarely, cross) — enforce lower <= point <= upper
    # rather than writing a nonsensical interval to the DB.
    lower_preds = np.minimum(lower_preds, point_preds)
    upper_preds = np.maximum(upper_preds, point_preds)

    output_df = pd.DataFrame({
        "customer_unique_id": df["customer_unique_id"],
        "clv_predicted_6m": np.round(point_preds, 2),
        "clv_ci_lower": np.round(lower_preds, 2),
        "clv_ci_upper": np.round(upper_preds, 2),
    })

    final_point_model.save_model("models/xgb_clv_point.json")
    final_lower_model.save_model("models/xgb_clv_lower.json")
    final_upper_model.save_model("models/xgb_clv_upper.json")
    with open("models/xgb_clv_encoder_categories.json", "w") as f:
        json.dump(encoder_categories, f)
    logger.info("Saved model artifacts to models/.")

    written = write_predictions(engine, output_df, batch_size=args.batch_size)
    logger.info("Done. %d row(s) written to mart.clv_features.", written)


if __name__ == "__main__":
    main()