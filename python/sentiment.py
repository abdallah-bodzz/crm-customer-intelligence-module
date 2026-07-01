"""
sentiment.py
=============
Phase 5, Step 1: reads mart.sentiment_scores.review_comment_message,
scores sentiment with LeIA (a Portuguese-lexicon fork of VADER), writes
compound_score + sentiment_label back to the same table.

WHY LeIA, NOT VADER:
VADER's lexicon is English-only. Run against Portuguese review text, it
doesn't error — it silently returns compound scores near 0 for most
reviews because it doesn't recognize the words, not because the reviews
are actually neutral. That's a wrong-but-plausible-looking result, which
is worse than a crash because nothing flags it. LeIA (rafjaa/LeIA) is a
deliberate fork of VADER with the lexicon rebuilt for Portuguese. Same
API shape (SentimentIntensityAnalyzer.polarity_scores), different
dictionary underneath.

Install (pin a commit, don't trust a moving default branch in a
portfolio project's requirements.txt):
    pip install git+https://github.com/rafjaa/LeIA.git

SCOPE:
Only rows where review_comment_message is non-NULL after cleaning are
scored. ~58.71% of Olist reviews have no text at all (Phase 2 EDA
finding) — those rows are left NULL here, on purpose. Imputing a score
for absent text is a downstream concern (customer_360.avg_sentiment_score
aggregation), not this script's job; conflating "no opinion expressed"
with "neutral opinion" at the row level would corrupt the per-review
table that other things may want to inspect directly.

USAGE:
    python sentiment.py                  # score all unscored rows
    python sentiment.py --force          # re-score everything, even rows that already have a compound_score
    python sentiment.py --dry-run        # score and report, write nothing
    python sentiment.py --batch-size 2000
"""

import argparse
import logging
import os
import sys
import time

import pandas as pd
from sqlalchemy import create_engine, text

import config
from text_cleaning import clean_review_text

try:
    import sys
    sys.path.insert(0, "LeIA_lib")
    from leia import SentimentIntensityAnalyzer
except ImportError:
    SentimentIntensityAnalyzer = None  # handled explicitly in main() — fail with a clear message, not a traceback


os.makedirs("logs", exist_ok=True)  # fresh clone won't have this yet; FileHandler below would crash on import otherwise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/sentiment_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log"),
    ],
)
logger = logging.getLogger("sentiment")

# LeIA/VADER's own documented threshold convention — not arbitrary, this
# is the same -0.05/+0.05 boundary the original VADER paper and LeIA's
# own docs use. Keeping it consistent with the upstream tool's convention
# means anyone who knows VADER/LeIA already knows what these labels mean.
POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05


def label_from_compound(compound: float) -> str:
    if compound >= POSITIVE_THRESHOLD:
        return "positive"
    if compound <= NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"


def fetch_reviews_to_score(engine, force: bool) -> pd.DataFrame:
    """
    Pull rows from mart.sentiment_scores that need scoring.
    force=False (default): only rows with NULL compound_score (skips
        already-scored rows — safe to re-run after a partial failure).
    force=True: re-score every row with non-NULL review_comment_message,
        even ones already scored (use after a LeIA version bump or a
        change to clean_review_text()).
    """
    where_clause = "review_comment_message IS NOT NULL"
    if not force:
        where_clause += " AND compound_score IS NULL"

    query = text(f"""
        SELECT review_id, review_comment_message
        FROM mart.sentiment_scores
        WHERE {where_clause}
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df


def score_batch(df: pd.DataFrame, analyzer) -> pd.DataFrame:
    """
    Clean + score one batch. Returns a DataFrame with review_id,
    compound_score, sentiment_label — rows whose text cleans down to
    nothing (e.g. a review that was only a URL) are dropped from the
    output, not scored as neutral.
    """
    records = []
    skipped_empty_after_cleaning = 0

    for review_id, raw_text in zip(df["review_id"], df["review_comment_message"]):
        cleaned = clean_review_text(raw_text)
        if cleaned is None:
            skipped_empty_after_cleaning += 1
            continue

        scores = analyzer.polarity_scores(cleaned)
        compound = round(float(scores["compound"]), 4)

        records.append({
            "review_id": review_id,
            "compound_score": compound,
            "sentiment_label": label_from_compound(compound),
        })

    if skipped_empty_after_cleaning:
        logger.info(
            "%d row(s) had text that cleaned down to nothing (e.g. URL-only) — left unscored.",
            skipped_empty_after_cleaning,
        )

    return pd.DataFrame.from_records(records, columns=["review_id", "compound_score", "sentiment_label"])


def write_scores(engine, scored_df: pd.DataFrame, batch_size: int) -> int:
    """
    Writes scores back row-by-row-batched via executemany-style UPDATE.
    Not a bulk MERGE — this table is small enough (one row per review,
    ~98K max) that a straightforward batched UPDATE is simpler to reason
    about and debug than a staging-table MERGE, and the cost difference
    at this row count is seconds, not minutes.
    """
    if scored_df.empty:
        return 0

    update_stmt = text("""
        UPDATE mart.sentiment_scores
        SET compound_score = :compound_score,
            sentiment_label = :sentiment_label,
            refreshed_at = SYSUTCDATETIME()
        WHERE review_id = :review_id
    """)

    total_written = 0
    records = scored_df.to_dict("records")

    with engine.begin() as conn:
        for start in range(0, len(records), batch_size):
            chunk = records[start:start + batch_size]
            conn.execute(update_stmt, chunk)
            total_written += len(chunk)
            logger.info("Wrote %d/%d rows...", total_written, len(records))

    return total_written


def main():
    parser = argparse.ArgumentParser(description="Score Olist review sentiment with LeIA.")
    parser.add_argument("--force", action="store_true",
                         help="Re-score rows that already have a compound_score.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Score and report counts/distribution, write nothing to the DB.")
    parser.add_argument("--batch-size", type=int, default=2000,
                         help="Rows per UPDATE batch (default 2000).")
    args = parser.parse_args()

    if SentimentIntensityAnalyzer is None:
        logger.error(
            "LeIA is not installed. Install with:\n"
            "    pip install git+https://github.com/rafjaa/LeIA.git\n"
            "Do NOT substitute vaderSentiment here — its English-only lexicon "
            "will silently mis-score Portuguese text instead of erroring."
        )
        sys.exit(1)

    engine = create_engine(config.CONNECTION_STRING)
    analyzer = SentimentIntensityAnalyzer()

    logger.info("Fetching reviews to score (force=%s)...", args.force)
    df = fetch_reviews_to_score(engine, force=args.force)
    logger.info("%d review(s) to score.", len(df))

    if df.empty:
        logger.info("Nothing to score. Exiting.")
        return

    scored_df = score_batch(df, analyzer)
    logger.info("Scored %d row(s).", len(scored_df))

    if not scored_df.empty:
        dist = scored_df["sentiment_label"].value_counts(normalize=True).round(4) * 100
        logger.info("Sentiment label distribution: %s", dist.to_dict())
        logger.info("Compound score stats: mean=%.4f, median=%.4f, std=%.4f",
                     scored_df["compound_score"].mean(),
                     scored_df["compound_score"].median(),
                     scored_df["compound_score"].std())

    if args.dry_run:
        logger.info("Dry run — no rows written to mart.sentiment_scores.")
        return

    written = write_scores(engine, scored_df, batch_size=args.batch_size)
    logger.info("Done. %d row(s) written to mart.sentiment_scores.", written)


if __name__ == "__main__":
    main()
