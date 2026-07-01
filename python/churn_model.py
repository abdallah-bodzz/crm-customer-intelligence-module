"""
churn_model.py
================
Phase 5, Step 4: predicts churn probability per customer. Trains an
XGBoost classifier on mart.customer_360, writes churn_probability +
is_churn_risk back to the same table.

==============================================================================
TWO THINGS CHECKED AND FIXED BEFORE WRITING ANY MODEL CODE
==============================================================================

1. LEAKAGE CHECK — CORRECTED AFTER A REAL RUN EXPOSED A MISS:
   First pass reasoning (kept here so the mistake is visible, not erased):
   "customer_360.is_churned and its candidate features... are ALL computed
   from a customer's FULL order history evaluated at the SAME single point
   in time. That's not leakage — both label and features are legitimate
   snapshots at the same observation point." That reasoning is correct for
   total_orders, total_gmv, avg_review_score, etc. — it is WRONG for
   days_since_last_order specifically, and the miss was caught by an actual
   training run, not by re-reading the code:

       is_churned = CASE WHEN days_since_last_order > @churn_window_days
                         THEN 1 ELSE 0 END

   days_since_last_order is not merely correlated with the label — it IS
   the label, unthresholded. Including it as a feature handed the model
   the exact split point (>180) it needed for perfect separation. Confirmed
   in a real run: F1=1.0, threshold tuned to 0.9999, confusion matrix with
   zero errors, and feature_importances_ showing days_since_last_order at
   1.0 with every other feature at 0.0 — the textbook signature of a
   feature that determines the label by construction. This is more severe
   than the customer_health_score exclusion below (a weighted composite
   that PARTLY reflects recency); this is a direct, total determination.

   FIX: days_since_last_order is REMOVED from FEATURE_COLUMNS_NUMERIC
   entirely. It is not usable as a churn-model feature under this label
   definition, full stop — not "use with caution," not "downweight it,"
   removed. A real churn model needs to predict departure from behavioral
   signals (order frequency, GMV, satisfaction, delivery experience)
   WITHOUT being handed the answer key.

   customer_health_score remains excluded for the original, distinct
   reason: it's a 40%-recency-weighted composite, which is a softer,
   partial form of the same problem, not literal identity with the label.

2. PIPELINE GAP CHECK:
   The Phase 5 plan calls for avg_sentiment_score as a churn_model.py
   input, "after sentiment.py runs." Checked: NOTHING in this pipeline
   ever actually aggregates mart.sentiment_scores.compound_score up to
   customer_360.avg_sentiment_score. sentiment.py's own docstring defers
   this as "a downstream concern" and no downstream script picks it up.
   That's a real gap, not a documentation nit — without a fix,
   avg_sentiment_score is NULL for 100% of customers forever, silently.

   FIX: backfill_avg_sentiment_score() below computes the aggregate
   directly from mart.sentiment_scores (which already carries
   customer_unique_id denormalized — confirmed in the Gold DDL, no
   extra joins needed) and writes it into customer_360 as a setup step
   before this script trains anything. This closes the gap at the point
   where it's actually needed, rather than leaving it as a silent NULL
   that quietly degrades the churn model's signal.

==============================================================================
MODEL
==============================================================================
XGBoost classifier, scale_pos_weight set from the EDA-locked ~71.18%
baseline churn rate (not the literal training batch's class balance,
which will drift slightly run to run — using the documented population
rate keeps this consistent with the rest of the project's "single source
of truth" discipline). Threshold tuned on F1 over a held-out split,
explicitly NOT the default 0.5 — with 71% positive class, 0.5 is not a
meaningful decision boundary.

USAGE:
    python churn_model.py
    python churn_model.py --dry-run
    python churn_model.py --threshold 0.35     # override the tuned threshold
    python churn_model.py --skip-sentiment-backfill   # use existing avg_sentiment_score as-is, don't recompute
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
from sklearn.metrics import (
    f1_score, precision_score, recall_score, roc_auc_score,
    precision_recall_curve, confusion_matrix,
)
from sklearn.model_selection import train_test_split
from sqlalchemy import create_engine, text

import config

os.makedirs("logs", exist_ok=True)
os.makedirs("models", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/churn_model_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("churn_model")

EDA_LOCKED_CHURN_RATE = 0.7118  # from Phase 2 EDA report — used for scale_pos_weight, not recomputed from the live batch (see module docstring)


# =============================================================================
# Gap fix: backfill avg_sentiment_score before it's needed as a feature
# =============================================================================

def backfill_avg_sentiment_score(engine) -> int:
    """
    Aggregates mart.sentiment_scores.compound_score up to
    customer_360.avg_sentiment_score. This belongs conceptually in
    sp_refresh_mart or a dedicated SQL step, but sentiment_scores is
    Python-filled (compound_score doesn't exist until sentiment.py has
    run), so a pure-SQL refresh can't compute this without knowing
    sentiment.py already ran. Doing it here, explicitly, as a setup
    step before training, rather than leaving the gap unfixed.

    Customers with zero scored reviews keep avg_sentiment_score = NULL
    here (not imputed to a default) — prepare_design_matrix() below
    handles the NULL with an explicit, visible imputation choice at
    model-input time, same discipline as clv_model.py's NULL handling.
    """
    query = text("""
        SELECT customer_unique_id, AVG(compound_score) AS avg_sentiment_score
        FROM mart.sentiment_scores
        WHERE compound_score IS NOT NULL
        GROUP BY customer_unique_id
    """)
    with engine.connect() as conn:
        agg_df = pd.read_sql(query, conn)

    if agg_df.empty:
        logger.warning(
            "No scored reviews found in mart.sentiment_scores — has sentiment.py been run? "
            "avg_sentiment_score will remain NULL for all customers."
        )
        return 0

    update_stmt = text("""
        UPDATE mart.customer_360
        SET avg_sentiment_score = :avg_sentiment_score
        WHERE customer_unique_id = :customer_unique_id
    """)
    records = agg_df.to_dict("records")
    with engine.begin() as conn:
        for start in range(0, len(records), 2000):
            conn.execute(update_stmt, records[start:start + 2000])

    logger.info("Backfilled avg_sentiment_score for %d customer(s).", len(records))
    return len(records)


# =============================================================================
# Data assembly
# =============================================================================

# customer_health_score excluded (40%-recency composite, partial circularity).
# days_since_last_order excluded — it IS is_churned's threshold variable,
# unthresholded. See module docstring, point 1, for the real run that
# exposed this and why it's a hard exclusion, not a judgment call.
FEATURE_COLUMNS_NUMERIC = [
    "total_orders",
    "total_gmv",
    "total_freight_paid",
    "avg_order_value",
    "avg_review_score",
    "pct_negative_reviews",
    "avg_sentiment_score",
    "avg_delivery_delta_days",
    "pct_late_deliveries",
    "total_categories_purchased",   # from clv_features, joined in below — same customer, not a leakage risk for THIS label
]
FEATURE_COLUMNS_CATEGORICAL = ["customer_state", "preferred_payment_type"]
TARGET_COLUMN = "is_churned"


def fetch_training_frame(engine) -> pd.DataFrame:
    query = text("""
        SELECT
            c.customer_unique_id, c.customer_state, c.is_churned,
            c.days_since_last_order, c.total_orders, c.total_gmv, c.total_freight_paid,
            c.avg_order_value, c.avg_review_score, c.pct_negative_reviews,
            c.avg_sentiment_score, c.avg_delivery_delta_days, c.pct_late_deliveries,
            clv.total_categories_purchased, clv.preferred_payment_type
        FROM mart.customer_360 c
        LEFT JOIN mart.clv_features clv ON clv.customer_unique_id = c.customer_unique_id
    """)
    # NOTE: days_since_last_order is fetched here for logging/diagnostics
    # only (e.g. inspecting a customer's raw standing) — it is deliberately
    # NOT in FEATURE_COLUMNS_NUMERIC above. Do not add it back without
    # re-reading the module docstring's leakage section; a real training
    # run already proved what happens if you do.
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def prepare_design_matrix(df: pd.DataFrame, encoder_categories: dict = None, fill_values: dict = None):
    """
    Same encoding pattern as clv_model.py's prepare_design_matrix, for
    consistency across the two scripts — unseen categories at predict
    time encode as all-zero rather than raising.

    fill_values: if given (captured at training time), reuses the exact
    same imputation values at predict time instead of recomputing medians
    from whatever subset happens to be passed in — this matters because
    this function is called once for train/test and will be called again
    for the full-dataset refit; using a freshly recomputed median each
    time would mean two slightly different imputation values for the
    "same" missing-data case, which is a subtle inconsistency worth
    avoiding deliberately rather than discovering it in a debugging session.
    """
    X_numeric = df[FEATURE_COLUMNS_NUMERIC].copy()

    # Domain-justified fallback for columns where EVEN THE MEDIAN is NaN —
    # this happens when a column is NULL for every row in the current
    # batch (confirmed in a real run: avg_sentiment_score was NULL for
    # 100% of customers because sentiment.py hadn't written anything yet —
    # a --dry-run, or simply running churn_model.py before sentiment.py).
    # X_numeric[col].median() on an all-NaN column returns NaN itself, so
    # .fillna(NaN) is a silent no-op — the column stays entirely NaN and
    # gets passed straight into XGBoost, which can tolerate NaN inputs but
    # will then learn nothing useful from a feature that's NaN for every
    # row, while throwing no error to flag that anything is wrong. Caught
    # via a "Mean of empty slice" RuntimeWarning in a real run's logs.
    DOMAIN_FALLBACK_FOR_ALL_NAN = {
        "avg_sentiment_score": 0.0,   # documented neutral midpoint of LeIA's -1..1 scale — same philosophy as sp_refresh_mart's population-median imputation, but a literal median can't be computed from zero non-null values
    }

    if fill_values is None:
        fill_values = {}
        for col in X_numeric.columns:
            if not X_numeric[col].isnull().any():
                continue
            median_val = X_numeric[col].median()
            if pd.isnull(median_val):
                fallback = DOMAIN_FALLBACK_FOR_ALL_NAN.get(col, 0.0)
                logging.getLogger("churn_model").warning(
                    "Column '%s' is NULL for 100%% of rows in this batch — median is undefined. "
                    "Falling back to %.4f. This is expected if sentiment.py hasn't run yet; "
                    "if it HAS run, this is a real data problem worth investigating.",
                    col, fallback,
                )
                median_val = fallback
            fill_values[col] = median_val

    for col, val in fill_values.items():
        if col in X_numeric.columns:
            X_numeric[col] = X_numeric[col].astype("float64").fillna(val)

    if encoder_categories is None:
        encoder_categories = {col: sorted(df[col].dropna().unique().tolist()) for col in FEATURE_COLUMNS_CATEGORICAL}

    dummies = []
    for col in FEATURE_COLUMNS_CATEGORICAL:
        cat = pd.Categorical(df[col], categories=encoder_categories[col])
        dummy = pd.get_dummies(cat, prefix=col)
        dummies.append(dummy)

    X = pd.concat([X_numeric.reset_index(drop=True)] + [d.reset_index(drop=True) for d in dummies], axis=1)
    return X, encoder_categories, fill_values


# =============================================================================
# Model + threshold tuning
# =============================================================================

def train_classifier(X_train, y_train) -> xgb.XGBClassifier:
    # scale_pos_weight from the EDA-locked population rate, not the live
    # batch's class balance — see module docstring for why.
    pos_weight = (1 - EDA_LOCKED_CHURN_RATE) / EDA_LOCKED_CHURN_RATE
    logger.info("scale_pos_weight = %.4f (from EDA-locked churn rate %.4f)", pos_weight, EDA_LOCKED_CHURN_RATE)

    model = xgb.XGBClassifier(
        objective="binary:logistic",
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=pos_weight,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(X_train, y_train)
    return model


def tune_threshold(model, X_val, y_val) -> tuple:
    """
    Sweeps thresholds against the precision-recall curve and picks the
    one maximizing F1 — explicitly not the default 0.5, which the Phase
    5 plan correctly flagged as wrong given the 71% positive class.
    Returns (best_threshold, best_f1, full_metrics_at_best).
    """
    probs = model.predict_proba(X_val)[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(y_val, probs)

    # precision_recall_curve returns thresholds of length n-1 relative to
    # precisions/recalls — align by dropping the last precision/recall point
    f1_scores = np.where(
        (precisions[:-1] + recalls[:-1]) > 0,
        2 * precisions[:-1] * recalls[:-1] / (precisions[:-1] + recalls[:-1]),
        0,
    )
    best_idx = np.argmax(f1_scores)
    best_threshold = float(thresholds[best_idx])
    best_f1 = float(f1_scores[best_idx])

    preds_at_best = (probs >= best_threshold).astype(int)
    metrics = {
        "threshold": best_threshold,
        "f1": best_f1,
        "precision": float(precision_score(y_val, preds_at_best)),
        "recall": float(recall_score(y_val, preds_at_best)),
        "roc_auc": float(roc_auc_score(y_val, probs)),
    }
    return best_threshold, best_f1, metrics


def write_predictions(engine, df: pd.DataFrame, batch_size: int) -> int:
    if df.empty:
        return 0

    update_stmt = text("""
        UPDATE mart.customer_360
        SET churn_probability = :churn_probability,
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
    parser = argparse.ArgumentParser(description="Churn classification (XGBoost, F1-tuned threshold).")
    parser.add_argument("--dry-run", action="store_true", help="Train + evaluate, write nothing to the DB.")
    parser.add_argument("--threshold", type=float, default=None,
                         help="Override the F1-tuned threshold with a manual value (e.g. --threshold 0.35).")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--skip-sentiment-backfill", action="store_true",
                         help="Skip recomputing avg_sentiment_score; use whatever is already in customer_360.")
    args = parser.parse_args()

    engine = create_engine(config.CONNECTION_STRING)

    if not args.skip_sentiment_backfill:
        logger.info("Backfilling avg_sentiment_score from mart.sentiment_scores (closes a real pipeline gap — see module docstring)...")
        backfill_avg_sentiment_score(engine)
    else:
        logger.info("Skipping sentiment backfill per --skip-sentiment-backfill.")

    logger.info("Fetching training frame...")
    df = fetch_training_frame(engine)
    logger.info("%d customer(s) loaded.", len(df))

    if df.empty:
        logger.error("No data. Has sp_refresh_mart been run? Exiting.")
        sys.exit(1)

    actual_churn_rate = df[TARGET_COLUMN].mean()
    logger.info("Actual churn rate in this batch: %.4f (EDA-locked reference: %.4f)", actual_churn_rate, EDA_LOCKED_CHURN_RATE)
    if abs(actual_churn_rate - EDA_LOCKED_CHURN_RATE) > 0.05:
        logger.warning(
            "Live churn rate differs from the EDA-locked reference by more than 5pp. "
            "This could mean as_of_date has moved significantly, or something upstream changed. "
            "Not blocking the run, but worth investigating before trusting these predictions."
        )

    X, encoder_categories, fill_values = prepare_design_matrix(df)
    y = df[TARGET_COLUMN]

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=args.test_size, random_state=42, stratify=y
    )
    logger.info("Train: %d. Validation: %d.", len(X_train), len(X_val))

    model = train_classifier(X_train, y_train)

    if args.threshold is not None:
        threshold = args.threshold
        probs_val = model.predict_proba(X_val)[:, 1]
        preds_val = (probs_val >= threshold).astype(int)
        metrics = {
            "threshold": threshold,
            "f1": float(f1_score(y_val, preds_val)),
            "precision": float(precision_score(y_val, preds_val)),
            "recall": float(recall_score(y_val, preds_val)),
            "roc_auc": float(roc_auc_score(y_val, probs_val)),
        }
        logger.info("Using manually-specified threshold=%.4f", threshold)
    else:
        threshold, best_f1, metrics = tune_threshold(model, X_val, y_val)
        logger.info("F1-tuned threshold=%.4f (F1=%.4f) — NOT the default 0.5, see module docstring.", threshold, best_f1)

    logger.info("Validation metrics at chosen threshold: %s", json.dumps(metrics, indent=2))

    cm = confusion_matrix(y_val, (model.predict_proba(X_val)[:, 1] >= threshold).astype(int))
    logger.info("Confusion matrix [[TN, FP], [FN, TP]]:\n%s", cm)

    feature_importance = pd.Series(model.feature_importances_, index=X.columns).sort_values(ascending=False)
    logger.info("Top 10 feature importances:\n%s", feature_importance.head(10).to_string())

    if args.dry_run:
        logger.info("Dry run — no rows written to mart.customer_360.")
        return

    logger.info("Refitting on full dataset for final predictions...")
    final_model = train_classifier(X, y)
    final_probs = final_model.predict_proba(X)[:, 1]

    output_df = pd.DataFrame({
        "customer_unique_id": df["customer_unique_id"],
        "churn_probability": np.round(final_probs, 4),
    })

    final_model.save_model("models/xgb_churn.json")
    with open("models/xgb_churn_meta.json", "w") as f:
        json.dump({
            "threshold": threshold,
            "encoder_categories": encoder_categories,
            "fill_values": fill_values,
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, f, indent=2)
    logger.info("Saved model + threshold metadata to models/.")

    written = write_predictions(engine, output_df, batch_size=args.batch_size)
    logger.info("Done. %d row(s) written to mart.customer_360. Tuned threshold for downstream "
                "is_churn_risk flagging: %.4f (apply this in run.py / the action-queue rule engine, "
                "not 0.5).", written, threshold)


if __name__ == "__main__":
    main()