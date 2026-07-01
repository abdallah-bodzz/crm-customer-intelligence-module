"""
next_purchase.py
==================
Phase 5, Step 5: predicts expected days until a customer's next purchase,
using survival analysis (Weibull AFT). Writes expected_next_purchase_days
back to mart.customer_360.

==============================================================================
THE GRAIN PROBLEM — read before editing
==============================================================================
This is the one model in the pipeline where the TRAINING grain and the
OUTPUT grain are genuinely different, and conflating them produces a
model that looks fine and is wrong.

Training grain: ONE ROW PER INTER-PURCHASE INTERVAL, not one row per
customer. A customer with N orders contributes:
  - (N-1) OBSERVED intervals: order_k -> order_(k+1), event=1
  - 1 CENSORED interval: last_order -> @as_of_date, event=0
    (we don't know if/when they'll order again — that's right-censoring,
    not "they never will")
A customer with exactly 2 orders contributes 1 observed + 1 censored row.
A customer with 5 orders contributes 4 observed + 1 censored row. This is
the standard way to build a survival dataset — collapsing each customer
to a single "average interval" row would throw away the censoring
information entirely, and a Weibull AFT model fit that way isn't doing
survival analysis, it's just a relabeled regression with extra steps.

Output grain: ONE ROW PER customer_unique_id, matching customer_360's
schema. After fitting on the interval-grain dataset, predictions are
made using each customer's OWN most recent censored row (their profile
+ how long they've already waited) — see predict_remaining_days().

THE conditional_after SUBTLETY:
lifelines.WeibullAFTFitter.predict_median() takes a conditional_after
parameter that computes T | T > s — "expected REMAINING time given the
subject has already survived s days," with the output ALREADY normalized
to start at 0 (i.e. it returns "days from now," not "days from order
date"). This is exactly what's needed, and lifelines does the conditional
survival-curve math correctly internally. The naive alternative —
predicting the full expected interval length and then subtracting
days_since_last_order by hand — would be a meaningfully different (and
wrong) calculation, since a Weibull hazard isn't linear; subtracting
after the fact ignores the actual shape of the survival curve and would
systematically distort the estimate for customers who are already
overdue. Use conditional_after, don't hand-roll the subtraction.

POPULATION:
Per the plan: customers with >=2 orders only (a customer with exactly 1
order has no observed interval to learn from — only a censored one,
which alone provides no signal about THEIR purchase cadence specifically,
though it still contributes to the population-level baseline hazard).
Single-order customers are excluded from this model's output entirely —
expected_next_purchase_days stays NULL for them. That's correct, not a
gap: this model answers "given a repeat-purchase pattern, when's the
next one," not "will a one-time buyer ever return" (that's churn_model's
job).

USAGE:
    python next_purchase.py
    python next_purchase.py --dry-run
"""

import argparse
import logging
import os
import sys
import time

import numpy as np
import pandas as pd
from lifelines import WeibullAFTFitter
from sqlalchemy import create_engine, text

import config

os.makedirs("logs", exist_ok=True)
os.makedirs("models", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/next_purchase_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("next_purchase")


# =============================================================================
# Canonical as_of_date — same discipline as every other script in this phase
# =============================================================================

def fetch_as_of_date(engine) -> str:
    query = text("SELECT as_of_date FROM mart.refresh_log WHERE refresh_id = 1")
    with engine.connect() as conn:
        result = conn.execute(query).fetchone()
    if result is None or result[0] is None:
        logger.error("mart.refresh_log has no as_of_date. Has sp_refresh_mart been run? Exiting.")
        sys.exit(1)
    return str(result[0])


# =============================================================================
# Build the interval-grain survival dataset
# =============================================================================

def fetch_order_dates(engine) -> pd.DataFrame:
    """One row per order, per customer, with profile covariates attached.
    Profile covariates (avg_order_value, etc.) come from customer_360/
    clv_features — same legitimate same-observation-point case as
    churn_model.py: a customer's own running average describes their
    standing profile, used to predict their own hazard. Not leakage."""
    query = text("""
        SELECT
            fo.customer_unique_id,
            fo.order_purchase_timestamp,
            c.customer_state,
            c.avg_order_value,
            c.avg_review_score,
            c.avg_sentiment_score,
            clv.total_categories_purchased
        FROM warehouse.fact_orders fo
        JOIN mart.customer_360 c ON c.customer_unique_id = fo.customer_unique_id
        LEFT JOIN mart.clv_features clv ON clv.customer_unique_id = fo.customer_unique_id
        WHERE fo.order_purchase_timestamp IS NOT NULL
        ORDER BY fo.customer_unique_id, fo.order_purchase_timestamp
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def build_survival_dataset(order_dates: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """
    Transforms order-level data into the interval-grain survival dataset
    described in the module docstring: (N-1) observed rows + 1 censored
    row per customer with >=2 orders. Customers with exactly 1 order are
    excluded entirely (no observed interval, and the plan's stated
    population rule is >=2 orders) — confirmed in the module docstring
    above as a deliberate scope boundary, not an oversight.
    """
    as_of = pd.Timestamp(as_of_date)
    rows = []

    for customer_id, group in order_dates.groupby("customer_unique_id", sort=False):
        dates = group["order_purchase_timestamp"].sort_values().tolist()
        if len(dates) < 2:
            continue  # single-order customers excluded — see module docstring

        covariates = group.iloc[0][[
            "customer_state", "avg_order_value", "avg_review_score",
            "avg_sentiment_score", "total_categories_purchased",
        ]].to_dict()

        # observed intervals: order_k -> order_(k+1)
        for k in range(len(dates) - 1):
            duration = (dates[k + 1] - dates[k]).days
            if duration <= 0:
                continue  # defensive: same-timestamp duplicate orders would otherwise create a zero/negative duration row that breaks the Weibull fit
            rows.append({
                "customer_unique_id": customer_id,
                "duration": duration,
                "event": 1,
                **covariates,
            })

        # final censored interval: last order -> as_of_date
        censored_duration = (as_of - dates[-1]).days
        if censored_duration > 0:
            rows.append({
                "customer_unique_id": customer_id,
                "duration": censored_duration,
                "event": 0,
                **covariates,
            })
        # if censored_duration <= 0 (last order IS as_of_date), there's no
        # remaining censored interval to record — skip rather than insert
        # a meaningless zero-duration row.

    df = pd.DataFrame(rows)
    logger.info(
        "Survival dataset: %d total row(s) (%d observed, %d censored) from %d customer(s) with >=2 orders.",
        len(df), (df["event"] == 1).sum() if not df.empty else 0,
        (df["event"] == 0).sum() if not df.empty else 0,
        df["customer_unique_id"].nunique() if not df.empty else 0,
    )
    return df


# =============================================================================
# Design matrix (shared shape with the other scripts' covariate handling)
# =============================================================================

NUMERIC_COVARIATES = ["avg_order_value", "avg_review_score", "avg_sentiment_score", "total_categories_purchased"]
CATEGORICAL_COVARIATES = ["customer_state"]


def prepare_design_matrix(df: pd.DataFrame, encoder_categories: dict = None, fill_values: dict = None):
    X_numeric = df[NUMERIC_COVARIATES].copy()

    # GUARD — this exact gap caused a real crash: "TypeError: NaNs were
    # detected in the dataset" from lifelines.fit(). Root cause: when
    # avg_sentiment_score is NULL for every row in the batch (e.g.
    # sentiment.py hasn't run yet, or this script is run via --dry-run
    # upstream so nothing was written), X_numeric[col].median() on an
    # all-NaN column returns NaN itself, so .fillna(NaN) silently does
    # nothing — the column stays entirely NaN, and unlike XGBoost (which
    # tolerates NaN inputs and just trains on a useless feature without
    # erroring, a quieter but equally real problem fixed the same way in
    # churn_model.py/clv_model.py), lifelines refuses to fit on NaN at all
    # and raises. The crash was the more honest failure mode of the two —
    # it's fixed the same way here regardless, for consistency.
    if fill_values is None:
        fill_values = {}
        for col in X_numeric.columns:
            if not X_numeric[col].isnull().any():
                continue
            median_val = X_numeric[col].median()
            if pd.isnull(median_val):
                fallback = 0.0  # avg_sentiment_score's documented neutral midpoint; total_categories_purchased/avg_review_score have no natural "neutral" but 0.0 is a defensible last resort that should never silently happen for those in practice (they're rarely all-NULL)
                logging.getLogger("next_purchase").warning(
                    "Column '%s' is NULL for 100%% of rows in this batch — median is undefined. "
                    "Falling back to %.4f. Expected if sentiment.py hasn't run yet; "
                    "otherwise investigate.", col, fallback,
                )
                median_val = fallback
            fill_values[col] = median_val

    for col, val in fill_values.items():
        if col in X_numeric.columns:
            X_numeric[col] = X_numeric[col].astype("float64").fillna(val)

    if encoder_categories is None:
        encoder_categories = {col: sorted(df[col].dropna().unique().tolist()) for col in CATEGORICAL_COVARIATES}

    dummies = []
    for col in CATEGORICAL_COVARIATES:
        cat = pd.Categorical(df[col], categories=encoder_categories[col])
        dummy = pd.get_dummies(cat, prefix=col)
        dummies.append(dummy)

    X = pd.concat([X_numeric.reset_index(drop=True)] + [d.reset_index(drop=True) for d in dummies], axis=1)
    return X, encoder_categories, fill_values


# =============================================================================
# Fit + predict
# =============================================================================

def fit_weibull_aft(survival_df: pd.DataFrame, X: pd.DataFrame) -> WeibullAFTFitter:
    fit_df = X.copy()
    fit_df["duration"] = survival_df["duration"].values
    fit_df["event"] = survival_df["event"].values

    model = WeibullAFTFitter(penalizer=0.01)  # small L2 penalty — covariate set includes one-hot state dummies, some Brazilian states have very few customers, penalizer keeps those coefficients from blowing up on sparse data
    model.fit(fit_df, duration_col="duration", event_col="event")
    return model


def predict_remaining_days(model: WeibullAFTFitter, customer_profiles: pd.DataFrame, X: pd.DataFrame) -> pd.DataFrame:
    """
    customer_profiles must have customer_unique_id + days_since_last_order
    (the "conditional_after" — how long they've already waited, using
    each customer's MOST RECENT order, i.e. their censored-row duration).
    Returns customer_unique_id + expected_next_purchase_days.

    predict_median with conditional_after returns the REMAINING expected
    time, already correctly conditioned on survival-so-far via the actual
    Weibull curve shape — not a naive subtraction. See module docstring.
    If a customer's survival curve never crosses 0.5 probability within
    a reasonable horizon, lifelines returns inf — those are converted to
    NULL (not a fake huge number) since "no defensible median estimate"
    is more honest than an arbitrarily large placeholder.
    """
    conditional_after = customer_profiles["days_since_last_order"].values
    median_predictions = model.predict_median(X, conditional_after=conditional_after)

    result = pd.DataFrame({
        "customer_unique_id": customer_profiles["customer_unique_id"].values,
        "expected_next_purchase_days": median_predictions.values,
    })
    result["expected_next_purchase_days"] = result["expected_next_purchase_days"].replace([np.inf, -np.inf], np.nan)
    return result


def write_predictions(engine, df: pd.DataFrame, batch_size: int) -> int:
    if df.empty:
        return 0

    update_stmt = text("""
        UPDATE mart.customer_360
        SET expected_next_purchase_days = :expected_next_purchase_days,
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
    parser = argparse.ArgumentParser(description="Next-purchase timing via Weibull AFT survival analysis.")
    parser.add_argument("--dry-run", action="store_true", help="Fit + report, write nothing to the DB.")
    parser.add_argument("--batch-size", type=int, default=2000)
    args = parser.parse_args()

    engine = create_engine(config.CONNECTION_STRING)
    as_of_date = fetch_as_of_date(engine)
    logger.info("as_of_date (from mart.refresh_log): %s", as_of_date)

    logger.info("Fetching order-level data...")
    order_dates = fetch_order_dates(engine)
    logger.info("%d order row(s) across %d customer(s).", len(order_dates), order_dates["customer_unique_id"].nunique())

    survival_df = build_survival_dataset(order_dates, as_of_date)
    if survival_df.empty:
        logger.warning("No customers with >=2 orders found. Nothing to model. Exiting.")
        return

    X, encoder_categories, fill_values = prepare_design_matrix(survival_df)

    logger.info("Fitting WeibullAFTFitter on %d interval-grain row(s)...", len(survival_df))
    model = fit_weibull_aft(survival_df, X)
    logger.info("Model fit. Coefficients (lambda_ param):\n%s", model.summary.loc["lambda_"][["coef", "p"]].to_string())

    # Concordance index — the survival-analysis equivalent of AUC, sanity
    # check that the model beats random ordering, not a number anyone
    # should over-interpret on its own.
    c_index = model.concordance_index_
    logger.info("Concordance index (train): %.4f (0.5 = random, 1.0 = perfect ranking)", c_index)
    if c_index < 0.6:
        n_eligible_customers = survival_df["customer_unique_id"].nunique()
        logger.warning(
            "Concordance index is weak (%.4f, barely above the 0.5 random baseline). Confirmed via a "
            "real run this is plausible given the population, not necessarily a bug: this model only "
            "ever sees customers with >=2 orders — %d of them here, out of the full customer base. Per "
            "the Phase 2 EDA's locked finding (96.88%% of customers are one-time buyers), that's "
            "already a small, atypical tail population, and a weak concordance there means the model "
            "found little genuine timing signal in WHO orders again soon vs. late, not that the code "
            "is broken. Check model.summary's p-values per covariate (logged above) before trusting "
            "any single feature's coefficient — most will likely be far from significant on a "
            "population this small. This is a real finding worth noting in the project writeup as a "
            "known limitation, not something to silently improve by tuning hyperparameters without "
            "first asking whether there's enough repeat-purchase signal in this dataset to model at all.",
            c_index, n_eligible_customers,
        )

    # Per-customer prediction: use each customer's MOST RECENT censored
    # row only (one prediction per customer, not one per interval row) —
    # this is the transition from interval-grain back to customer-grain.
    censored_rows = survival_df[survival_df["event"] == 0].copy()
    censored_rows = censored_rows.rename(columns={"duration": "days_since_last_order"})
    X_censored, _, _ = prepare_design_matrix(censored_rows, encoder_categories=encoder_categories, fill_values=fill_values)

    logger.info("Predicting expected_next_purchase_days for %d customer(s) with an active censored interval...", len(censored_rows))
    predictions = predict_remaining_days(model, censored_rows, X_censored)

    n_inf = predictions["expected_next_purchase_days"].isnull().sum()
    if n_inf:
        logger.info(
            "%d customer(s) had a survival curve that never crosses 50%% probability within the model's horizon "
            "(no defensible median estimate) — left as NULL rather than a placeholder number.", n_inf
        )

    valid_preds = predictions["expected_next_purchase_days"].dropna()
    if not valid_preds.empty:
        logger.info(
            "expected_next_purchase_days stats: mean=%.1f, median=%.1f, min=%.1f, max=%.1f",
            valid_preds.mean(), valid_preds.median(), valid_preds.min(), valid_preds.max(),
        )

    if args.dry_run:
        logger.info("Dry run — no rows written to mart.customer_360.")
        return

    import pickle
    with open("models/weibull_aft.pkl", "wb") as f:
        pickle.dump({"model": model, "encoder_categories": encoder_categories, "fill_values": fill_values}, f)
    logger.info("Saved model artifact to models/weibull_aft.pkl.")

    written = write_predictions(engine, predictions.dropna(subset=["expected_next_purchase_days"]), batch_size=args.batch_size)
    logger.info("Done. %d row(s) written to mart.customer_360. (%d customer(s) left NULL — see note above, "
                "and %d customer(s) with <2 orders were never in scope for this model.)",
                written, n_inf, order_dates["customer_unique_id"].nunique() - survival_df["customer_unique_id"].nunique())


if __name__ == "__main__":
    main()