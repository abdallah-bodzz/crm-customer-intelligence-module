"""
action_rules.py
================
Phase 5, Step 6 — CRM Action Queue Rule Engine.

Reads mart.vw_customer_health (flat post-ML view) + mart.clv_features,
applies a priority-ordered rule set from config/action_rules.json,
and writes one action record per customer to mart.crm_action_queue.

DESIGN DECISIONS — read before modifying:

1. RULES LIVE IN JSON, NOT HERE.
   config/action_rules.json owns all thresholds and rule definitions.
   This script is the engine; the config is the policy. CLI flags override
   the JSON at runtime for testing — they do NOT modify the file.

2. EVALUATION ORDER IS EXPLICIT AND MATTERS.
   Rules are evaluated in the order listed in config["evaluation_order"].
   First match wins. MONITOR is always last — exhaustive fallback.
   If you add a rule, insert it BEFORE MONITOR in evaluation_order or
   it will never fire.

3. WRITE MODE IS TRUNCATE + INSERT — CURRENT-STATE, NOT APPEND.
   crm_action_queue is a current-state snapshot: "what to do with each
   customer RIGHT NOW." History is in mart.action_run_log (one audit row
   per execution), not in the queue itself.

4. UNMATCHED CUSTOMERS ARE A BUG, NOT A FEATURE.
   MONITOR is unconditional — any customer that exits without a match
   means the rule set has a gap. The script raises an error (not a
   warning) if n_unmatched > 0. A silent NULL in action_type is worse
   than a loud pipeline failure.

5. TRIGGER REASON IS HUMAN-READABLE.
   trigger_reason is a plain-English explanation of WHY a rule fired,
   including actual threshold values. This is what a CRM analyst reads.

6. SCHEMA CONTRACT — verified against actual DDL before writing:
   crm_action_queue columns: action_id (IDENTITY, skip), customer_unique_id,
       action_type, priority, churn_probability, clv_predicted, trigger_reason,
       created_at (DEFAULT SYSUTCDATETIME(), skip).
   action_type CHECK constraint: RETENTION_CAMPAIGN, REACTIVATION,
       VIP_UPGRADE, MONITOR. AT_RISK_NURTURE is not in the constraint —
       see rule config for how at-risk customers are handled within the
       four allowed action types.
   vw_customer_health column names: is_churned (not is_churn_risk),
       avg_delivery_delta_days (not avg_delivery_delta).

7. USES utils.py SHARED INFRASTRUCTURE.
   setup_logging(), get_engine(), fetch_df(), fetch_refresh_log(),
   batched_update() — no copy-paste from other scripts.

USAGE:
    python action_rules.py                          # run with config defaults
    python action_rules.py --dry-run                # classify + report, write nothing
    python action_rules.py --churn-threshold 0.5    # override churn threshold
    python action_rules.py --clv-percentile 75      # override CLV split percentile
    python action_rules.py --vip-percentile 85      # override VIP CLV threshold
    python action_rules.py --config path/to/alt.json
    python action_rules.py --dry-run --churn-threshold 0.4
"""

__version__ = "1.1.0"

import argparse
import copy
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent))
import config
from utils import setup_logging, get_engine, fetch_df, fetch_refresh_log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SCRIPT_DIR = Path(__file__).parent
# DEFAULT_CONFIG_PATH = SCRIPT_DIR.parent / "config" / "action_rules.json"
SCRIPT_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "action_rules.json" # relative to script


# Must exactly match crm_action_queue CHECK constraint
VALID_ACTION_TYPES = {"RETENTION_CAMPAIGN", "REACTIVATION", "VIP_UPGRADE", "MONITOR"}
VALID_PRIORITIES   = {"HIGH", "MED", "LOW"}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    """
    Load and validate action_rules.json. Fails loud at startup on any
    structural error — better than a confusing mid-run crash.
    """
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found: {config_path}\n"
            f"Expected: config/action_rules.json relative to python/.\n"
            f"Pass --config <path> to specify an alternate location."
        )

    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    required = {"rules", "evaluation_order", "write_mode", "audit"}
    missing = required - set(cfg.keys())
    if missing:
        raise ValueError(f"action_rules.json missing required keys: {missing}")

    for rule_name in cfg["evaluation_order"]:
        if rule_name not in cfg["rules"]:
            raise ValueError(
                f"evaluation_order references '{rule_name}' which is not in rules."
            )

    if "MONITOR" not in cfg["evaluation_order"]:
        raise ValueError("MONITOR must be in evaluation_order — it is the exhaustive fallback.")

    if cfg["evaluation_order"][-1] != "MONITOR":
        raise ValueError(
            f"MONITOR must be LAST in evaluation_order. "
            f"Currently at position {cfg['evaluation_order'].index('MONITOR')}."
        )

    # Validate all action_type values against the CHECK constraint
    for rule_name, rule_def in cfg["rules"].items():
        action_type = rule_def.get("action_type", rule_name)
        if action_type not in VALID_ACTION_TYPES:
            raise ValueError(
                f"Rule '{rule_name}' has action_type '{action_type}' which is not "
                f"in crm_action_queue CHECK constraint: {VALID_ACTION_TYPES}.\n"
                f"Either change the action_type in the JSON, or add the value to "
                f"the CHECK constraint in 05_crm_action_queue.sql."
            )

    return cfg


def apply_cli_overrides(cfg: dict, args: argparse.Namespace, logger: logging.Logger) -> dict:
    """
    Apply CLI threshold overrides to a deep copy of cfg.
    Does NOT write to the JSON file. Logs every override explicitly
    so the audit trail reflects what actually ran.
    """
    cfg = copy.deepcopy(cfg)

    if args.churn_threshold is not None:
        for rule_name in ["RETENTION_CAMPAIGN", "REACTIVATION"]:
            conds = cfg["rules"].get(rule_name, {}).get("conditions", {})
            for key in ("churn_probability_gte", "churn_probability_lt"):
                if key in conds:
                    old = conds[key]
                    conds[key] = args.churn_threshold
                    logger.info("CLI override: %s.%s %s → %s", rule_name, key, old, args.churn_threshold)

    if args.clv_percentile is not None:
        for rule_name in ["RETENTION_CAMPAIGN", "REACTIVATION"]:
            conds = cfg["rules"].get(rule_name, {}).get("conditions", {})
            for key in ("clv_percentile_gte", "clv_percentile_lt"):
                if key in conds:
                    old = conds[key]
                    conds[key] = args.clv_percentile
                    logger.info("CLI override: %s.%s %s → %s", rule_name, key, old, args.clv_percentile)

    if args.vip_percentile is not None:
        conds = cfg["rules"].get("VIP_UPGRADE", {}).get("conditions", {})
        if "clv_percentile_gte" in conds:
            old = conds["clv_percentile_gte"]
            conds["clv_percentile_gte"] = args.vip_percentile
            logger.info("CLI override: VIP_UPGRADE.clv_percentile_gte %s → %s", old, args.vip_percentile)

    return cfg


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

# Columns verified against actual DDL of vw_customer_health and clv_features:
#   is_churned         (not is_churn_risk — that column does not exist)
#   avg_delivery_delta_days  (not avg_delivery_delta — that column does not exist)
CUSTOMER_QUERY = """
SELECT
    c.customer_unique_id,
    c.churn_probability,
    c.is_churned,
    c.rfm_segment,
    c.km_cluster,
    c.health_tier,
    c.customer_health_score,
    c.total_gmv,
    c.avg_review_score,
    c.avg_delivery_delta_days,
    c.days_since_last_order,
    c.total_orders,
    c.customer_state,
    cf.clv_predicted_6m,
    cf.avg_order_value,
    cf.tenure_months
FROM mart.vw_customer_health c
LEFT JOIN mart.clv_features cf
    ON c.customer_unique_id = cf.customer_unique_id
"""


def fetch_customers(engine, logger: logging.Logger) -> pd.DataFrame:
    logger.info("Fetching customer data from mart.vw_customer_health + mart.clv_features...")
    df = fetch_df(engine, CUSTOMER_QUERY)
    logger.info("Fetched %d customers.", len(df))

    required = {"customer_unique_id", "churn_probability", "rfm_segment", "clv_predicted_6m"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"Required columns missing from query result: {missing_cols}.\n"
            f"Has sp_refresh_mart and the full ML pipeline been run?"
        )

    null_churn = df["churn_probability"].isna().sum()
    null_clv   = df["clv_predicted_6m"].isna().sum()
    null_seg   = df["rfm_segment"].isna().sum()

    if null_churn > 0:
        logger.warning(
            "%d customers have NULL churn_probability — churn_model.py may not have run.", null_churn
        )
    if null_clv > 0:
        logger.info(
            "%d customers (%.1f%%) have NULL clv_predicted_6m — "
            "single-order customers excluded from CLV model. "
            "They receive clv_percentile=0 and fall to MONITOR or lower-priority rules.",
            null_clv, 100 * null_clv / len(df),
        )
    if null_seg > 0:
        logger.warning(
            "%d customers have NULL rfm_segment — segmentation.py may not have run.", null_seg
        )

    return df


def compute_clv_percentiles(df: pd.DataFrame, logger: logging.Logger) -> pd.Series:
    """
    Per-customer CLV percentile rank (0–100) against customers WITH a
    CLV prediction. NULL CLV → percentile 0 (treated as bottom-tier).
    This is the correct business behaviour: we don't upgrade customers
    whose value we can't estimate.
    """
    clv   = df["clv_predicted_6m"].copy()
    has   = clv.notna()
    pct   = pd.Series(0.0, index=df.index)

    if has.sum() > 0:
        pct[has] = clv[has].rank(pct=True) * 100

    logger.info(
        "CLV percentile distribution — p25: %.1f  p50: %.1f  p75: %.1f  p90: %.1f",
        pct.quantile(0.25), pct.quantile(0.50),
        pct.quantile(0.75), pct.quantile(0.90),
    )
    return pct


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

def matches_rule(row: pd.Series, clv_pct: float, rule_def: dict) -> bool:
    """
    Test all conditions for one rule. First unmet condition short-circuits.
    NULL-safe: a NULL churn_probability never satisfies churn_probability_gte.
    """
    conds = rule_def.get("conditions", {})
    if not conds:
        return True  # MONITOR — unconditional catch-all

    churn = row.get("churn_probability")
    rfm   = row.get("rfm_segment", "")

    if "churn_probability_gte" in conds:
        if churn is None or (isinstance(churn, float) and np.isnan(churn)):
            return False
        if churn < conds["churn_probability_gte"]:
            return False

    if "churn_probability_lt" in conds:
        if churn is None or (isinstance(churn, float) and np.isnan(churn)):
            return False
        if churn >= conds["churn_probability_lt"]:
            return False

    if "clv_percentile_gte" in conds:
        if clv_pct < conds["clv_percentile_gte"]:
            return False

    if "clv_percentile_lt" in conds:
        if clv_pct >= conds["clv_percentile_lt"]:
            return False

    if "rfm_segment_in" in conds:
        if rfm not in conds["rfm_segment_in"]:
            return False

    if "is_churned" in conds:
        if bool(row.get("is_churned", 0)) != conds["is_churned"]:
            return False

    return True


def build_trigger_reason(
    rule_name: str,
    rule_def: dict,
    row: pd.Series,
    clv_pct: float,
    effective_thresholds: dict,
) -> str:
    """
    Human-readable explanation of why this rule fired for this customer.
    Includes actual values — not just the rule name. This is what a CRM
    analyst reads when they ask "why is this customer flagged?"
    """
    conds     = rule_def.get("conditions", {})
    churn     = row.get("churn_probability")
    clv_val   = row.get("clv_predicted_6m")
    rfm_seg   = row.get("rfm_segment", "unknown")
    health    = row.get("health_tier", "unknown")

    churn_str = f"{churn:.3f}" if churn is not None and not np.isnan(float(churn)) else "N/A"
    clv_str   = f"R${clv_val:.2f}" if clv_val is not None and not np.isnan(float(clv_val)) else "NULL"
    pct_str   = f"{clv_pct:.0f}th pct"

    thresh_churn = effective_thresholds.get("churn_threshold", 0.6)

    if rule_name == "RETENTION_CAMPAIGN":
        return (
            f"Churn risk {churn_str} ≥ {thresh_churn:.2f}; "
            f"CLV {clv_str} at {pct_str} "
            f"(≥ {conds.get('clv_percentile_gte', 50)}th) — "
            f"high-value customer, premium retention warranted"
        )
    elif rule_name == "REACTIVATION":
        return (
            f"Churn risk {churn_str} ≥ {thresh_churn:.2f}; "
            f"CLV {clv_str} at {pct_str} "
            f"(< {conds.get('clv_percentile_lt', 50)}th) — "
            f"lower-value at-risk, cost-efficient reactivation"
        )
    elif rule_name == "VIP_UPGRADE":
        segs = conds.get("rfm_segment_in", [])
        return (
            f"Segment: {rfm_seg} (in {segs}); "
            f"CLV {clv_str} at {pct_str} "
            f"(≥ {conds.get('clv_percentile_gte', 90)}th) — "
            f"VIP candidate for loyalty programme or account manager"
        )
    elif rule_name == "MONITOR":
        return (
            f"No high-priority conditions met — "
            f"churn={churn_str}, segment={rfm_seg}, "
            f"CLV {clv_str} at {pct_str}, health={health}"
        )
    else:
        # Generic fallback for any additional custom rules added via JSON
        return (
            f"Rule '{rule_name}' matched — "
            f"churn={churn_str}, segment={rfm_seg}, CLV {clv_str} at {pct_str}"
        )


def classify_customers(
    df: pd.DataFrame,
    clv_percentiles: pd.Series,
    cfg: dict,
    effective_thresholds: dict,
    logger: logging.Logger,
) -> pd.DataFrame:
    """
    Apply rule engine to every customer. Returns DataFrame with:
        customer_unique_id, action_type, priority, churn_probability,
        clv_predicted_6m, trigger_reason
    One row per customer. n_unmatched must be 0 — raises RuntimeError otherwise.
    """
    evaluation_order = cfg["evaluation_order"]
    rules            = cfg["rules"]
    results          = []
    n_unmatched      = 0

    # Reset index for reliable iloc access on clv_percentiles
    df_reset  = df.reset_index(drop=True)
    pct_reset = clv_percentiles.reset_index(drop=True)

    for idx in range(len(df_reset)):
        row     = df_reset.iloc[idx]
        clv_pct = float(pct_reset.iloc[idx])
        matched = False

        for rule_name in evaluation_order:
            rule_def = rules[rule_name]

            if not rule_def.get("enabled", True):
                continue

            if matches_rule(row, clv_pct, rule_def):
                # action_type may be overridden in the JSON (e.g. a rule named
                # "HIGH_VALUE_WINBACK" that writes action_type="RETENTION_CAMPAIGN").
                # Default: rule name IS the action type.
                action_type = rule_def.get("action_type", rule_name)
                results.append({
                    "customer_unique_id": row["customer_unique_id"],
                    "action_type":        action_type,
                    "priority":           rule_def["priority"],
                    "churn_probability":  row.get("churn_probability"),
                    "clv_predicted_6m":   row.get("clv_predicted_6m"),  # bound as :clv_predicted_6m in SQL
                    "trigger_reason":     build_trigger_reason(
                        rule_name, rule_def, row, clv_pct, effective_thresholds
                    ),
                })
                matched = True
                break

        if not matched:
            n_unmatched += 1
            logger.error(
                "UNMATCHED customer %s — churn=%s rfm=%s clv_pct=%.1f",
                row.get("customer_unique_id"),
                row.get("churn_probability"),
                row.get("rfm_segment"),
                clv_pct,
            )

    if n_unmatched > 0:
        raise RuntimeError(
            f"{n_unmatched} customer(s) matched no rule. "
            f"MONITOR should be unconditional — check it has no conditions "
            f"and is last in evaluation_order."
        )

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def log_distribution(result_df: pd.DataFrame, logger: logging.Logger) -> dict:
    """
    Log counts and percentages by action_type and priority.
    Returns count dict for the audit log row.
    """
    total  = len(result_df)
    counts = {}

    logger.info("=" * 65)
    logger.info("ACTION DISTRIBUTION  (%d customers total)", total)
    logger.info("=" * 65)

    for action_type, count in result_df["action_type"].value_counts().items():
        pct = 100 * count / total
        logger.info("  %-25s  %6d  (%5.1f%%)", action_type, count, pct)
        counts[action_type] = int(count)

    logger.info("-" * 65)
    logger.info("PRIORITY BREAKDOWN")
    for priority, count in result_df["priority"].value_counts().items():
        pct = 100 * count / total
        logger.info("  %-10s  %6d  (%5.1f%%)", priority, count, pct)

    n_actionable = total - counts.get("MONITOR", 0)
    n_high       = int(result_df["priority"].eq("HIGH").sum())
    logger.info("=" * 65)
    logger.info(
        "HEADLINE: %d customers flagged for action (%.1f%%)",
        n_actionable, 100 * n_actionable / total,
    )
    logger.info(
        "HEADLINE: %d HIGH-priority — immediate attention required", n_high
    )

    logger.info("-" * 65)
    logger.info("PER-ACTION SEGMENT STATS (avg churn / avg CLV):")
    for action_type in result_df["action_type"].unique():
        sub       = result_df[result_df["action_type"] == action_type]
        avg_churn = sub["churn_probability"].mean()
        avg_clv   = sub["clv_predicted_6m"].mean()
        logger.info(
            "  %-25s  churn=%.3f   CLV=%s",
            action_type,
            avg_churn if not np.isnan(avg_churn) else 0.0,
            f"R${avg_clv:.2f}" if avg_clv is not None and not np.isnan(avg_clv) else "N/A",
        )
    logger.info("=" * 65)

    return counts


# ---------------------------------------------------------------------------
# Database writes
# ---------------------------------------------------------------------------

# Columns verified against actual crm_action_queue DDL:
#   action_id    -> IDENTITY, skip
#   clv_predicted -> table column name (not clv_predicted_6m)
#   created_at   -> DEFAULT SYSUTCDATETIME(), skip
#   rfm_segment, health_tier, is_actioned -> do NOT exist in table
#
# The DataFrame column clv_predicted_6m is bound to :clv_predicted_6m and
# inserted as clv_predicted — the binding parameter name and the column name
# are intentionally different here; this is the mapping point.
INSERT_ACTION_SQL = """
INSERT INTO mart.crm_action_queue (
    customer_unique_id,
    action_type,
    priority,
    churn_probability,
    clv_predicted,
    trigger_reason
)
VALUES (
    :customer_unique_id,
    :action_type,
    :priority,
    :churn_probability,
    :clv_predicted_6m,
    :trigger_reason
)
"""

INSERT_RUN_LOG_SQL = """
INSERT INTO mart.action_run_log (
    run_timestamp, run_by, script_version,
    churn_threshold_used, clv_percentile_used, vip_percentile_used,
    write_mode,
    n_retention_campaign, n_reactivation, n_vip_upgrade,
    n_at_risk_nurture, n_monitor, n_total,
    n_priority_high, n_priority_med, n_priority_low,
    n_customers_in, n_customers_unmatched,
    config_snapshot, run_notes
)
VALUES (
    :run_timestamp, :run_by, :script_version,
    :churn_threshold_used, :clv_percentile_used, :vip_percentile_used,
    :write_mode,
    :n_retention_campaign, :n_reactivation, :n_vip_upgrade,
    :n_at_risk_nurture, :n_monitor, :n_total,
    :n_priority_high, :n_priority_med, :n_priority_low,
    :n_customers_in, :n_customers_unmatched,
    :config_snapshot, :run_notes
)
"""


def _nan_to_none(records: list) -> list:
    """Replace float NaN with None for SQL NULL compatibility."""
    for r in records:
        for k, v in r.items():
            if isinstance(v, float) and np.isnan(v):
                r[k] = None
    return records


def write_action_queue(
    engine, result_df: pd.DataFrame, batch_size: int, logger: logging.Logger
) -> int:
    """
    TRUNCATE crm_action_queue + INSERT new snapshot inside one transaction.
    A crash mid-INSERT rolls back to the previous state — not to empty.
    """
    records = _nan_to_none(result_df.to_dict("records"))
    insert_stmt = text(INSERT_ACTION_SQL)

    logger.info("Truncating mart.crm_action_queue...")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE mart.crm_action_queue"))
        logger.info("Inserting %d action records (batch_size=%d)...", len(records), batch_size)
        total = 0
        for start in range(0, len(records), batch_size):
            chunk = records[start : start + batch_size]
            conn.execute(insert_stmt, chunk)
            total += len(chunk)
            logger.info("  Inserted %d / %d", total, len(records))

    logger.info("Write complete — %d rows in mart.crm_action_queue.", total)
    return total


def write_run_log(
    engine,
    cfg: dict,
    result_df: pd.DataFrame,
    counts: dict,
    effective_thresholds: dict,
    n_customers_in: int,
    dry_run: bool,
    logger: logging.Logger,
):
    """
    Append one summary row to mart.action_run_log.
    Skipped if audit.write_run_log = false in config.
    Non-fatal if the table doesn't exist yet — logs a warning and continues.
    """
    if not cfg.get("audit", {}).get("write_run_log", True):
        logger.info("audit.write_run_log = false — skipping run log.")
        return

    by_priority = result_df["priority"].value_counts() if not result_df.empty else {}

    try:
        run_by = os.getlogin()
    except Exception:
        run_by = None

    record = {
        "run_timestamp":        datetime.now(timezone.utc).isoformat(),
        "run_by":               run_by,
        "script_version":       __version__,
        "churn_threshold_used": effective_thresholds.get("churn_threshold", 0.6),
        "clv_percentile_used":  effective_thresholds.get("clv_percentile", 50),
        "vip_percentile_used":  effective_thresholds.get("vip_percentile", 90),
        "write_mode":           "DRY_RUN" if dry_run else "TRUNCATE_INSERT",
        "n_retention_campaign": counts.get("RETENTION_CAMPAIGN", 0),
        "n_reactivation":       counts.get("REACTIVATION", 0),
        "n_vip_upgrade":        counts.get("VIP_UPGRADE", 0),
        "n_at_risk_nurture":    counts.get("AT_RISK_NURTURE", 0),
        "n_monitor":            counts.get("MONITOR", 0),
        "n_total":              len(result_df),
        "n_priority_high":      int(by_priority.get("HIGH", 0)),
        "n_priority_med":       int(by_priority.get("MED", 0)),
        "n_priority_low":       int(by_priority.get("LOW", 0)),
        "n_customers_in":       n_customers_in,
        "n_customers_unmatched": 0,
        "config_snapshot":      json.dumps(cfg["rules"], ensure_ascii=False),
        "run_notes":            "DRY RUN — crm_action_queue not modified" if dry_run else None,
    }

    try:
        with engine.begin() as conn:
            conn.execute(text(INSERT_RUN_LOG_SQL), [record])
        logger.info("Audit row written to mart.action_run_log.")
    except Exception as e:
        logger.warning(
            "Could not write to mart.action_run_log: %s\n"
            "If the table doesn't exist yet, run sql/03_mart/09_action_run_log.sql.\n"
            "The action queue was written successfully — this is non-fatal.",
            e,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "CRM Action Queue Rule Engine. Classifies every customer into "
            "RETENTION_CAMPAIGN / REACTIVATION / VIP_UPGRADE / MONITOR "
            "and writes to mart.crm_action_queue."
        ),
        epilog=(
            "Threshold overrides apply to this run only — they do NOT modify "
            "config/action_rules.json. Edit the JSON for permanent changes."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Classify all customers and log distribution — write nothing to the DB.",
    )
    parser.add_argument(
        "--churn-threshold", type=float, default=None, metavar="FLOAT",
        help="Override churn_probability_gte (e.g. 0.5). Default: from config JSON.",
    )
    parser.add_argument(
        "--clv-percentile", type=int, default=None, metavar="INT",
        help="Override CLV percentile split for RETENTION vs REACTIVATION (e.g. 75). Default: 50.",
    )
    parser.add_argument(
        "--vip-percentile", type=int, default=None, metavar="INT",
        help="Override VIP_UPGRADE CLV percentile threshold (e.g. 85). Default: 90.",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, metavar="PATH",
        help=f"Path to action_rules.json. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--batch-size", type=int, default=2000, metavar="INT",
        help="INSERT batch size (default 2000).",
    )
    return parser.parse_args()


def main():
    args   = parse_args()
    logger = setup_logging("action_rules")

    logger.info("=" * 70)
    logger.info("CRM Action Queue Rule Engine  v%s", __version__)
    logger.info("Config  : %s", args.config)
    logger.info("Dry run : %s", args.dry_run)
    logger.info("=" * 70)

    # 1. Load + validate config
    cfg = load_config(args.config)
    logger.info(
        "Config loaded — %d rules, order: %s",
        len(cfg["rules"]), cfg["evaluation_order"],
    )

    # 2. Capture effective thresholds (before CLI overrides modify cfg)
    effective_thresholds = {
        "churn_threshold": (
            args.churn_threshold
            or cfg["rules"]["RETENTION_CAMPAIGN"]["conditions"].get("churn_probability_gte", 0.6)
        ),
        "clv_percentile": (
            args.clv_percentile
            or cfg["rules"]["RETENTION_CAMPAIGN"]["conditions"].get("clv_percentile_gte", 50)
        ),
        "vip_percentile": (
            args.vip_percentile
            or cfg["rules"]["VIP_UPGRADE"]["conditions"].get("clv_percentile_gte", 90)
        ),
    }

    # 3. Apply CLI overrides to a copy of cfg
    cfg = apply_cli_overrides(cfg, args, logger)

    # 4. DB connection
    engine = get_engine()

    # 5. Verify mart has been built (refresh_log must exist and have a row)
    try:
        rl = fetch_refresh_log(engine)
        logger.info(
            "refresh_log: as_of_date=%s  ml_cutoff=%s  churn_window=%d days",
            rl["as_of_date"], rl["ml_cutoff_date"], rl["churn_window_days"],
        )
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    # 6. Fetch
    df = fetch_customers(engine, logger)
    n_customers_in = len(df)
    if df.empty:
        logger.error("No customer data returned. Has sp_refresh_mart been run?")
        sys.exit(1)

    # 7. CLV percentiles
    clv_percentiles = compute_clv_percentiles(df, logger)

    # 8. Classify
    t0        = time.time()
    result_df = classify_customers(df, clv_percentiles, cfg, effective_thresholds, logger)
    logger.info("Classification: %.2fs for %d customers.", time.time() - t0, len(df))

    # 9. Report
    counts = log_distribution(result_df, logger)

    # 10. Write
    if args.dry_run:
        logger.info("DRY RUN — mart.crm_action_queue was NOT modified.")
        write_run_log(
            engine, cfg, result_df, counts, effective_thresholds,
            n_customers_in, dry_run=True, logger=logger,
        )
        return

    write_action_queue(engine, result_df, batch_size=args.batch_size, logger=logger)
    write_run_log(
        engine, cfg, result_df, counts, effective_thresholds,
        n_customers_in, dry_run=False, logger=logger,
    )
    logger.info("Done. Action queue populated.")


if __name__ == "__main__":
    main()