"""
text_cleaning.py
=================
Lightweight, lexicon-safe cleaning for Olist review text before it goes
into LeIA for sentiment scoring.

Why this exists as its own module, not inline in sentiment.py:
Text pre-processing is a reusable utility, not sentiment-specific glue
code — if a future script (topic modeling, a supervised classifier, etc.)
ever needs the same cleaning, it imports this, it doesn't copy-paste it.

Why this is INTENTIONALLY light:
LeIA (like VADER) is lexicon- and rule-based — it scores sentiment by
matching whole words and short patterns (negation, intensifiers,
degree modifiers) against a hand-built dictionary. Aggressive NLP
preprocessing — stemming, stopword removal, lowercasing punctuation
that LeIA uses as an intensity signal (e.g. "!!!", ALL CAPS) — would
strip exactly the signal LeIA is designed to use. This module only
removes things that are unambiguously noise: HTML/copy-paste artifacts,
URLs, and Brazilian-specific formatting (R$ money mentions, CPF/order
number strings) that have no sentiment value and can fragment tokens
LeIA would otherwise match cleanly.

Reference: cleaning patterns adapted from the regex approach in the
"E-Commerce Sentiment Analysis EDA Viz NLP" Kaggle notebook (line breaks,
hyperlinks, money, numbers) — adapted to this project's lighter-touch
philosophy, not copied wholesale. That notebook's version also strips
stopwords and stems for a downstream TF-IDF classifier; this project's
sentiment step doesn't train a classifier, so those steps are dropped.
"""

import re
import unicodedata

# Compiled once at import time — these run over ~40K+ review rows per
# sentiment.py execution, so avoiding re-compilation per row matters.
_RE_LINE_BREAKS = re.compile(r'[\r\n\t]+')
_RE_URLS = re.compile(r'https?://\S+|www\.\S+')
_RE_HTML_TAGS = re.compile(r'<[^>]+>')                  # <br/>, <p>, etc. — copy-pasted reviews sometimes carry these
_RE_HTML_ENTITIES = re.compile(r'&[a-zA-Z]+;|&#\d+;')
_RE_MONEY_BRL = re.compile(r'r\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?', re.IGNORECASE)
_RE_ORDER_REFS = re.compile(r'\b\d{6,}\b')              # long digit runs (order/tracking numbers) — no sentiment value, can break tokenization
_RE_REPEATED_PUNCT = re.compile(r'([!?.])\1{2,}')        # "!!!!!" -> "!!" (keep SOME repetition: LeIA's booster logic reads emphasis from punctuation, don't erase it entirely, just cap it)
_RE_REPEATED_CHARS = re.compile(r'([a-zA-ZÀ-ÿ])\1{3,}')   # "ótimoooooo" -> "ótimooo" — keep elongation as a (mild) signal, just bound it so it doesn't break word matching
_RE_EXTRA_WHITESPACE = re.compile(r'\s{2,}')


def clean_review_text(text: str) -> str | None:
    """
    Clean a single Olist review_comment_message for LeIA scoring.

    Returns None for None/empty/whitespace-only input — callers should
    treat None as "no text to score", not "score this as neutral".
    Empty string after cleaning (e.g. a review that was ONLY a URL) is
    also normalized to None for the same reason.

    Deliberately does NOT: lowercase, strip stopwords, stem, or remove
    all punctuation/repetition. LeIA needs that signal intact.
    """
    if text is None:
        return None

    cleaned = str(text)

    # Normalize unicode (NFC) — Olist's PT-BR text can carry combining
    # diacritics in inconsistent forms depending on the export path;
    # normalizing avoids LeIA's lexicon silently missing a word because
    # "á" was encoded as two codepoints instead of one.
    cleaned = unicodedata.normalize('NFC', cleaned)

    cleaned = _RE_URLS.sub(' ', cleaned)
    cleaned = _RE_HTML_TAGS.sub(' ', cleaned)
    cleaned = _RE_HTML_ENTITIES.sub(' ', cleaned)
    cleaned = _RE_LINE_BREAKS.sub(' ', cleaned)
    cleaned = _RE_MONEY_BRL.sub(' ', cleaned)
    cleaned = _RE_ORDER_REFS.sub(' ', cleaned)
    cleaned = _RE_REPEATED_PUNCT.sub(r'\1\1', cleaned)
    cleaned = _RE_REPEATED_CHARS.sub(r'\1\1\1', cleaned)
    cleaned = _RE_EXTRA_WHITESPACE.sub(' ', cleaned)
    cleaned = cleaned.strip()

    return cleaned if cleaned else None


def clean_review_batch(texts: list) -> list:
    """Vectorized-style convenience wrapper for a list/Series of raw texts."""
    return [clean_review_text(t) for t in texts]
