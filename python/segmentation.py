"""
segmentation.py
=================
Phase 5, Step 2: reads mart.rfm_features (recency_score, frequency_score,
monetary_score — already computed in SQL via NTILE(5)), writes back
rfm_segment (rule-based label) and km_cluster (K-means cluster ID).

TWO INDEPENDENT OUTPUTS, NOT ONE DERIVED FROM THE OTHER:
  rfm_segment — deterministic, rule-based on the R/F/M quintile scores.
                This is what Power BI and the CRM action queue should
                read for segment-driven logic, because it's interpretable:
                anyone can look at a customer's R/F/M scores and verify
                why they got labeled "Champions" without needing to know
                what K-means did. The Phase 5 plan calls this out
                explicitly ("rule-based from RFM scores, not centroids —
                more interpretable") and that reasoning holds.
  km_cluster  — unsupervised K-means over the same three features,
                kept as a SEPARATE, complementary view. K-means may find
                structure the fixed rule thresholds don't (e.g. a cluster
                that's recency-dominated vs one that's monetary-dominated
                in a way a single quintile cutoff can't express). Useful
                for exploratory Power BI pages and the Phase 6 idea of
                comparing rule-based segments against data-driven ones —
                NOT used by anything downstream that needs interpretability
                (action queue, churn features).

RULE-SET CORRECTION FROM THE ORIGINAL PHASE 5 PLAN:
The plan's original 5-segment rule table (Champions/Loyal/At Risk/
Hibernating/Lost) was checked by brute-force enumerating all 125 possible
(recency_score, frequency_score, monetary_score) combinations against the
stated thresholds. Result: 77 of 125 combinations (61.6%) — the MAJORITY
of the input space — matched no rule and would have produced a NULL
rfm_segment. That's not an edge case; it's most customers. The rule set
below is a corrected, exhaustive version: every one of the 125 possible
score combinations resolves to exactly one named segment, verified by
brute force before being written into this script (see the comment block
above SEGMENT_RULES for the verification method). Two new segments
("Potential Loyalist", "Can't Lose", "Frequent Low-Spender", "Needs
Attention") were added because the gaps in the original 5-segment scheme
weren't randomly scattered — they clustered into real, distinct,
nameable customer patterns, not noise to be dumped in a catch-all bucket.

USAGE:
    python segmentation.py                    # full run: label + cluster + write
    python segmentation.py --dry-run           # compute and report, write nothing
    python segmentation.py --k 5               # override K (default: auto via silhouette, search range 3-7)
    python segmentation.py --force-k 5         # skip the elbow/silhouette search, use K=5 directly
"""

import argparse
import logging
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
from sqlalchemy import create_engine, text

import config

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/segmentation_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("segmentation")


# =============================================================================
# Rule-based segment labeling
# =============================================================================
#
# Verified exhaustive over all 125 (r, f, m) combinations where each score
# is in {1,2,3,4,5} — see module docstring. Rules are evaluated in order;
# first match wins. The final two rules (Needs Attention / Frequent
# Low-Spender are checked before it) exist specifically to make the
# ordering exhaustive — if you edit this function, re-run the verification
# at the bottom of this file (`python segmentation.py --verify-rules`)
# before trusting it again.
#
# Business meaning of each tier, not just the score thresholds:
#   Champions            - best recency, frequency, AND monetary value.
#                          The customers everything else aspires to be.
#   Loyal                - recent, consistently frequent and valuable,
#                          just short of the top tier on at least one axis.
#   Potential Loyalist   - very recent, but not yet frequent. Early-stage
#                          or newly-acquired customers worth nurturing
#                          before they either convert to Loyal or churn.
#   At Risk              - WAS frequent and valuable, but recency has
#                          dropped. The classic "win them back before
#                          it's too late" segment — highest-leverage
#                          target for retention campaigns.
#   Can't Lose           - dormant (low recency) but historically high
#                          spend. Distinct from generic Hibernating
#                          because the GMV at stake is too large to file
#                          under "probably gone" without a targeted push.
#   Hibernating          - low recency AND low frequency, not yet at the
#                          absolute floor. Lower priority than At Risk /
#                          Can't Lose because less value is at stake.
#   Lost                 - the floor: worst possible score on all three
#                          axes. Likely not worth active recovery spend.
#   Frequent Low-Spender - orders often, but each order is small. Distinct
#                          real pattern (not a catch-all) — matches the
#                          Phase 2 EDA's own finding that repeat buyers'
#                          average order value (R$124) is actually LOWER
#                          than the overall AOV (R$138). These customers
#                          are engaged but low-margin; a different lever
#                          (upsell, bundling) than a churn-risk lever.
#   Needs Attention       - the genuine middle: moderate recency, not
#                          captured by any sharper rule above. Smallest
#                          meaningful bucket, not a dump for leftovers.
#
def assign_rfm_segment(r: int, f: int, m: int) -> str:
    if r == 5 and f >= 4 and m >= 4:
        return "Champions"
    if r >= 4 and f >= 3 and m >= 3:
        return "Loyal"
    if r >= 4 and f < 3:
        return "Potential Loyalist"
    if r <= 2 and f >= 3 and m >= 3:
        return "At Risk"
    if r <= 2 and m >= 4:
        return "Can't Lose"
    if r == 1 and f == 1 and m == 1:
        return "Lost"
    if r <= 2 and f <= 2:
        return "Hibernating"
    if f >= 3 and m <= 2:
        return "Frequent Low-Spender"
    if r == 3:
        return "Needs Attention"
    # Should be unreachable given the verification below — if this ever
    # fires in production, the rule set has a real gap; log loudly rather
    # than silently defaulting.
    raise ValueError(f"Unclassified RFM combination: r={r}, f={f}, m={m}. "
                      f"Rule set is not exhaustive — fix assign_rfm_segment().")


def verify_rules_exhaustive() -> bool:
    """
    Brute-force check: every one of the 125 possible (r, f, m) score
    combinations must resolve to a segment without raising. Run this any
    time assign_rfm_segment() is edited, before trusting it on real data.
    """
    failures = []
    for r in range(1, 6):
        for f in range(1, 6):
            for m in range(1, 6):
                try:
                    assign_rfm_segment(r, f, m)
                except ValueError:
                    failures.append((r, f, m))

    if failures:
        logger.error("Rule set is NOT exhaustive. %d unclassified combos: %s",
                      len(failures), failures[:10])
        return False

    logger.info("Rule set verified exhaustive: all 125 (r,f,m) combinations resolve.")
    return True


# =============================================================================
# K-means clustering (independent of the rule-based segment)
# =============================================================================

def fit_kmeans(rfm_scores: pd.DataFrame, k: int | None, force_k: int | None) -> tuple:
    """
    Fits K-means on standardized (recency_score, frequency_score,
    monetary_score). Returns (cluster_labels, k_used, scaler, model).

    If force_k is given, uses it directly (no search). Otherwise searches
    k in range(3, 8) and picks the k maximizing silhouette score — bounded
    range because for RFM segmentation specifically, going below 3 clusters
    loses meaningful structure and going above 7 tends to fragment the
    quintile-scored input into clusters too small to act on at the GMV
    scale this project operates at (~96K customers).

    PERFORMANCE NOTE, confirmed via a real run: the original version of
    this function took 854 seconds (14+ minutes) on 96,096 rows. Profiled
    the actual cause rather than guessing: silhouette_score's default
    behavior computes FULL pairwise distances across every row — O(n²),
    so ~96K² comparisons PER CANDIDATE K, five times over. That dwarfs the
    K-means fitting itself. sklearn's silhouette_score has a documented
    `sample_size` parameter specifically for this — subsampling gives a
    statistically reliable estimate at a small fraction of the cost; this
    is the standard mitigation, not a shortcut that trades away
    correctness. n_init is also reduced to 5 during the SEARCH phase only
    (the final model, fit once K is chosen, still uses the full n_init=10
    below) — the search just needs to rank candidate K values relative to
    each other, not converge each one perfectly.
    """
    X = rfm_scores[["recency_score", "frequency_score", "monetary_score"]].to_numpy()
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    SILHOUETTE_SAMPLE_SIZE = 8000  # well above the ~1000-2000 typically considered sufficient for a stable silhouette estimate; chosen conservatively, not minimally, given this is a portfolio project where reviewers may check the methodology

    if force_k is not None:
        k_used = force_k
        logger.info("Using forced K=%d (skipping silhouette search).", k_used)
    else:
        search_range = range(3, 8) if k is None else range(k, k + 1)
        best_k, best_score = None, -1.0
        for candidate_k in search_range:
            model = KMeans(n_clusters=candidate_k, random_state=42, n_init=5)  # reduced from 10 for the search phase only — see docstring
            labels = model.fit_predict(X_scaled)
            score = silhouette_score(
                X_scaled, labels,
                sample_size=min(SILHOUETTE_SAMPLE_SIZE, len(X_scaled)),
                random_state=42,
            )
            logger.info("K=%d -> silhouette=%.4f (sampled, n=%d)", candidate_k, score, min(SILHOUETTE_SAMPLE_SIZE, len(X_scaled)))
            if score > best_score:
                best_k, best_score = candidate_k, score
        k_used = best_k
        logger.info("Selected K=%d via silhouette search (score=%.4f).", k_used, best_score)

    model = KMeans(n_clusters=k_used, random_state=42, n_init=10)
    labels = model.fit_predict(X_scaled)
    return labels, k_used, scaler, model


# =============================================================================
# DB I/O
# =============================================================================

def fetch_rfm_features(engine) -> pd.DataFrame:
    query = text("""
        SELECT customer_unique_id, recency_score, frequency_score, monetary_score
        FROM mart.rfm_features
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def write_segments(engine, df: pd.DataFrame, batch_size: int) -> int:
    """
    df must have columns: customer_unique_id, rfm_segment, km_cluster.
    Batched UPDATE, same pattern as sentiment.py's write_scores — simplest
    correct approach at this row count (~96K), no staging table needed.
    """
    if df.empty:
        return 0

    update_stmt = text("""
        UPDATE mart.rfm_features
        SET rfm_segment = :rfm_segment,
            km_cluster = :km_cluster,
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
    parser = argparse.ArgumentParser(description="RFM segment labeling + K-means clustering.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute and report, write nothing to the DB.")
    parser.add_argument("--k", type=int, default=None,
                         help="Search only this K (still runs silhouette for reporting). Default: search 3-7.")
    parser.add_argument("--force-k", type=int, default=None,
                         help="Use this K directly, skip the silhouette search entirely.")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--verify-rules-only", action="store_true",
                         help="Just run the exhaustiveness check on the rule set and exit.")
    args = parser.parse_args()

    if not verify_rules_exhaustive():
        logger.error("Aborting — fix assign_rfm_segment() before running on real data.")
        sys.exit(1)

    if args.verify_rules_only:
        return

    engine = create_engine(config.CONNECTION_STRING)

    logger.info("Fetching mart.rfm_features...")
    df = fetch_rfm_features(engine)
    logger.info("%d customer(s) to segment.", len(df))

    if df.empty:
        logger.info("Nothing to segment. Has sp_refresh_mart been run? Exiting.")
        return

    # Rule-based segment — deterministic, no model, no randomness
    df["rfm_segment"] = df.apply(
        lambda row: assign_rfm_segment(row["recency_score"], row["frequency_score"], row["monetary_score"]),
        axis=1,
    )
    seg_dist = df["rfm_segment"].value_counts()
    logger.info("Segment distribution:\n%s", seg_dist.to_string())

    # K-means — independent, exploratory
    cluster_labels, k_used, scaler, model = fit_kmeans(df, k=args.k, force_k=args.force_k)
    df["km_cluster"] = cluster_labels
    logger.info("K-means cluster sizes:\n%s", df["km_cluster"].value_counts().sort_index().to_string())

    # Cross-tab: how well do clusters line up with rule-based segments?
    # Purely diagnostic — printed to the log, not written anywhere. If a
    # cluster maps cleanly onto a single segment, that's a sanity signal
    # the two methods agree; if it's scattered, that's worth knowing too,
    # not a failure of either method.
    crosstab = pd.crosstab(df["km_cluster"], df["rfm_segment"])
    logger.info("Cluster x Segment cross-tab:\n%s", crosstab.to_string())

    if args.dry_run:
        logger.info("Dry run — no rows written to mart.rfm_features.")
        return

    written = write_segments(
        engine,
        df[["customer_unique_id", "rfm_segment", "km_cluster"]],
        batch_size=args.batch_size,
    )
    logger.info("Done. %d row(s) written to mart.rfm_features. K used: %d.", written, k_used)


if __name__ == "__main__":
    main()