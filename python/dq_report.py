"""
dq_report.py
=============
Generates reports/dq_report.html — a data quality report focused
specifically on ML PREDICTION quality, run AFTER the Phase 5 pipeline
has executed.

==============================================================================
WHY THIS SCOPE, NOT A GENERIC "CHECK EVERYTHING" REPORT
==============================================================================
Three layers of this project already have their own verification:
  - sql/02_warehouse/09_verify_silver.sql checks Bronze->Silver integrity
    (SCD2 correctness, orphans, payment reconciliation).
  - sql/03_mart/07_verify_mart.sql checks Silver->Gold integrity (row
    parity, refresh_log consistency, GMV/freight reconciliation).
  - Each Phase 5 script (sentiment.py, segmentation.py, clv_model.py,
    churn_model.py, next_purchase.py) logs its own holdout metrics,
    distributions, and sanity checks at training time.

Re-implementing any of that here would be redundant noise, not new
signal — checked this deliberately before writing a line of code (see
the design reasoning that produced this file). What NONE of those layers
can do:
  1. Check prediction columns AFTER the full Python pipeline has run —
     the SQL verify scripts execute BEFORE Python writes anything, so
     they structurally cannot know whether churn_probability,
     clv_predicted_6m, rfm_segment, km_cluster, compound_score, or
     expected_next_purchase_days ended up populated, NULL, or out of a
     sane range.
  2. Cross-check TWO different scripts' outputs against each other —
     e.g. whether the SQL-computed rule-based is_churned flag roughly
     agrees with churn_model.py's churn_probability. That comparison
     spans Gold's own column and Python's prediction column
     simultaneously; no single script's internal logging does this.
  3. Produce a PERSISTENT artifact. Training-time logs scroll by once;
     an HTML file in reports/ is something a person (or a hiring
     manager looking at the repo) can open after the fact.

That's this script's entire job: post-pipeline ML prediction quality,
nothing this project already checks elsewhere.

USAGE:
    python dq_report.py                  # writes reports/dq_report.html
    python dq_report.py --open           # also opens it in a browser
"""

import argparse
import logging
import os
import sys
import time
import webbrowser

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import config

os.makedirs("logs", exist_ok=True)
os.makedirs("../reports", exist_ok=True)  # repo layout: python/ and reports/ are siblings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/dq_report_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("dq_report")

REPORT_PATH = "../reports/dq_report.html"


# =============================================================================
# Checks — each returns a dict the HTML renderer turns into one section.
# Every check is defensive about missing data (e.g. Python hasn't run
# yet) — reports a "not yet populated" status rather than crashing, since
# this script is explicitly meant to be runnable at any point in the
# pipeline's lifecycle, not just after a fully successful --all run.
# =============================================================================

def check_prediction_coverage(engine) -> dict:
    """NULL rates for every Python-filled column, across all mart tables."""
    # CLV columns live in mart.clv_features, not customer_360
    clv_query = text("""
        SELECT
            COUNT(*) AS total_customers,
            SUM(CASE WHEN clv_predicted_6m IS NULL THEN 1 ELSE 0 END) AS clv_predicted_6m_null
        FROM mart.clv_features
    """)
    c360_query = text("""
        SELECT
            COUNT(*) AS total_customers,
            SUM(CASE WHEN churn_probability IS NULL THEN 1 ELSE 0 END) AS churn_probability_null,
            SUM(CASE WHEN avg_sentiment_score IS NULL THEN 1 ELSE 0 END) AS avg_sentiment_score_null,
            SUM(CASE WHEN expected_next_purchase_days IS NULL THEN 1 ELSE 0 END) AS expected_next_purchase_days_null
        FROM mart.customer_360
    """)
    rfm_query = text("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN rfm_segment IS NULL THEN 1 ELSE 0 END) AS rfm_segment_null,
               SUM(CASE WHEN km_cluster IS NULL THEN 1 ELSE 0 END) AS km_cluster_null
        FROM mart.rfm_features
    """)
    sentiment_query = text("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN compound_score IS NULL THEN 1 ELSE 0 END) AS compound_score_null
        FROM mart.sentiment_scores
        WHERE review_comment_message IS NOT NULL
    """)

    with engine.connect() as conn:
        clv = pd.read_sql(clv_query, conn).iloc[0]
        c360 = pd.read_sql(c360_query, conn).iloc[0]
        rfm = pd.read_sql(rfm_query, conn).iloc[0]
        sent = pd.read_sql(sentiment_query, conn).iloc[0]

    def pct(n, total):
        return round(100 * n / total, 2) if total else None

    return {
        "title": "Prediction Coverage",
        "rows": [
            ("churn_probability populated", 
             f"{c360['total_customers'] - c360['churn_probability_null']}/{c360['total_customers']}",
             f"{pct(c360['total_customers'] - c360['churn_probability_null'], c360['total_customers'])}%"),
            ("clv_predicted_6m populated", 
             f"{clv['total_customers'] - clv['clv_predicted_6m_null']}/{clv['total_customers']}",
             f"{pct(clv['total_customers'] - clv['clv_predicted_6m_null'], clv['total_customers'])}%"),
            ("avg_sentiment_score populated", 
             f"{c360['total_customers'] - c360['avg_sentiment_score_null']}/{c360['total_customers']}",
             f"{pct(c360['total_customers'] - c360['avg_sentiment_score_null'], c360['total_customers'])}%"),
            ("expected_next_purchase_days populated", 
             f"{c360['total_customers'] - c360['expected_next_purchase_days_null']}/{c360['total_customers']}",
             f"{pct(c360['total_customers'] - c360['expected_next_purchase_days_null'], c360['total_customers'])}% (expected <100% — single-order customers are out of scope, see next_purchase.py)"),
            ("rfm_segment populated", 
             f"{rfm['total'] - rfm['rfm_segment_null']}/{rfm['total']}",
             f"{pct(rfm['total'] - rfm['rfm_segment_null'], rfm['total'])}% (expected 100% — rule set is verified exhaustive)"),
            ("km_cluster populated", 
             f"{rfm['total'] - rfm['km_cluster_null']}/{rfm['total']}",
             f"{pct(rfm['total'] - rfm['km_cluster_null'], rfm['total'])}%"),
            ("compound_score populated (of reviews with text)", 
             f"{sent['total'] - sent['compound_score_null']}/{sent['total']}",
             f"{pct(sent['total'] - sent['compound_score_null'], sent['total'])}% (expected 100% if sentiment.py completed)"),
        ],
    }


def check_prediction_ranges(engine) -> dict:
    """Sanity bounds — values a correct model literally cannot produce."""
    c360_query = text("""
        SELECT
            MIN(churn_probability) AS min_churn_prob, MAX(churn_probability) AS max_churn_prob,
            MIN(avg_sentiment_score) AS min_sentiment, MAX(avg_sentiment_score) AS max_sentiment,
            MIN(expected_next_purchase_days) AS min_npd, MAX(expected_next_purchase_days) AS max_npd
        FROM mart.customer_360
    """)
    clv_query = text("""
        SELECT MIN(clv_predicted_6m) AS min_clv, MAX(clv_predicted_6m) AS max_clv
        FROM mart.clv_features
    """)

    with engine.connect() as conn:
        c360 = pd.read_sql(c360_query, conn).iloc[0]
        clv = pd.read_sql(clv_query, conn).iloc[0]

    def in_range(val, lo, hi):
        if pd.isnull(val):
            return "N/A (not yet populated)"
        return "OK" if lo <= val <= hi else f"OUT OF RANGE (expected [{lo}, {hi}])"

    return {
        "title": "Prediction Range Sanity",
        "rows": [
            ("churn_probability range", f"[{c360['min_churn_prob']}, {c360['max_churn_prob']}]",
             in_range(c360['min_churn_prob'], 0, 1) if not pd.isnull(c360['min_churn_prob']) else "N/A"),
            ("clv_predicted_6m range", f"[{clv['min_clv']}, {clv['max_clv']}]",
             "OK" if pd.isnull(clv['min_clv']) or clv['min_clv'] >= 0 else "OUT OF RANGE (negative GMV prediction)"),
            ("avg_sentiment_score range", f"[{c360['min_sentiment']}, {c360['max_sentiment']}]",
             in_range(c360['min_sentiment'], -1, 1) if not pd.isnull(c360['min_sentiment']) else "N/A"),
            ("expected_next_purchase_days range", f"[{c360['min_npd']}, {c360['max_npd']}]",
             "OK" if pd.isnull(c360['min_npd']) or c360['min_npd'] >= 0 else "OUT OF RANGE (negative duration)"),
        ],
    }


def check_rule_vs_model_agreement(engine) -> dict:
    """
    The cross-check no single script can do: does the SQL-computed
    rule-based is_churned flag roughly agree with churn_model.py's
    churn_probability? Large disagreement is a real signal — either the
    model learned something genuinely different from the simple 180-day
    rule (interesting, worth investigating), or something is wrong in
    how features were assembled (also worth investigating). Either way,
    this is information only available by joining Gold's own column to
    Python's prediction column, which is exactly what this script is for.
    """
    query = text("""
        SELECT is_churned, churn_probability
        FROM mart.customer_360
        WHERE churn_probability IS NOT NULL
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    if df.empty:
        return {"title": "Rule vs. Model Churn Agreement", "rows": [("Status", "Not yet available", "churn_model.py has not run")]}

    # Using 0.5 here is NOT the tuned threshold — this section is a
    # descriptive sanity check on agreement at a neutral midpoint, not a
    # judgment about the model's actual deployed threshold (that's
    # recorded separately in models/xgb_churn_meta.json by churn_model.py).
    df["model_flag_at_0.5"] = (df["churn_probability"] >= 0.5).astype(int)
    agreement = (df["is_churned"] == df["model_flag_at_0.5"]).mean()

    rule_churn_rate = df["is_churned"].mean()
    model_avg_prob = df["churn_probability"].mean()

    return {
        "title": "Rule vs. Model Churn Agreement",
        "rows": [
            ("Rule-based churn rate (is_churned)", f"{rule_churn_rate:.4f}", "EDA-locked reference: 0.7118"),
            ("Model's average predicted churn_probability", f"{model_avg_prob:.4f}",
             "Should be reasonably close to the rule-based rate, not wildly different"),
            ("Agreement rate at 0.5 cutoff (descriptive only, not the deployed threshold)", f"{agreement:.4f}",
             "Low agreement isn't necessarily wrong, but is worth a manual look"),
        ],
    }


def check_segment_distribution(engine) -> dict:
    """Persistent artifact for segmentation.py's distribution — no segment should be at 0, per the verified-exhaustive rule set."""
    query = text("SELECT rfm_segment, COUNT(*) AS n FROM mart.rfm_features GROUP BY rfm_segment ORDER BY n DESC")
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    if df.empty or df["rfm_segment"].isnull().all():
        return {"title": "RFM Segment Distribution", "rows": [("Status", "Not yet available", "segmentation.py has not run")]}

    total = df["n"].sum()
    rows = [(seg if seg else "(NULL — should not happen, rule set is verified exhaustive)", str(n), f"{100*n/total:.1f}%")
            for seg, n in zip(df["rfm_segment"], df["n"])]
    return {"title": "RFM Segment Distribution", "rows": rows}


# =============================================================================
# HTML rendering — plain, dependency-free (no Jinja2 needed for a report
# this size; an f-string template is honest about what this actually is).
# =============================================================================

def render_section(section: dict) -> str:
    rows_html = "\n".join(
        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td class='status'>{r[2]}</td></tr>"
        for r in section["rows"]
    )
    return f"""
    <div class="section">
        <h2>{section['title']}</h2>
        <table>
            <thead><tr><th>Check</th><th>Value</th><th>Status / Note</th></tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """


def render_report(sections: list, generated_at: str) -> str:
    sections_html = "\n".join(render_section(s) for s in sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CRM Customer Intelligence — ML Data Quality Report</title>
<style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px; margin: 40px auto; color: #1a1a2e; background: #f7f7fb; }}
    h1 {{ font-size: 1.6rem; border-bottom: 3px solid #4361ee; padding-bottom: 10px; }}
    .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 30px; }}
    .section {{ background: white; border-radius: 8px; padding: 20px 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    h2 {{ font-size: 1.1rem; color: #2b2d42; margin-top: 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
    th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid #e0e0e8; color: #555; }}
    td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
    .status {{ font-family: monospace; font-size: 0.85rem; }}
    .footer-note {{ color: #888; font-size: 0.85rem; margin-top: 30px; }}
</style>
</head>
<body>
    <h1>CRM Customer Intelligence — ML Data Quality Report</h1>
    <div class="meta">Generated: {generated_at}</div>
    {sections_html}
    <p class="footer-note">
        This report covers ML PREDICTION quality only — Bronze/Silver integrity is
        checked by sql/02_warehouse/09_verify_silver.sql, and Silver/Gold integrity
        by sql/03_mart/07_verify_mart.sql. This file does not duplicate those checks;
        it covers what only becomes checkable after the Phase 5 Python pipeline runs.
    </p>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate the post-pipeline ML data quality report.")
    parser.add_argument("--open", action="store_true", help="Open the generated report in a browser after writing it.")
    args = parser.parse_args()

    engine = create_engine(config.CONNECTION_STRING)

    logger.info("Running prediction quality checks...")
    sections = [
        check_prediction_coverage(engine),
        check_prediction_ranges(engine),
        check_rule_vs_model_agreement(engine),
        check_segment_distribution(engine),
    ]

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    html = render_report(sections, generated_at)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Report written to %s", REPORT_PATH)

    if args.open:
        webbrowser.open(f"file://{os.path.abspath(REPORT_PATH)}")


if __name__ == "__main__":
    main()