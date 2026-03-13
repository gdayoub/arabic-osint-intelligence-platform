"""Text cleaning pipeline for Arabic news articles."""

from __future__ import annotations

from src.processing.normalize_arabic import normalize_arabic_text

# Lightweight stopword set; can be replaced by larger curated list.
ARABIC_STOPWORDS = {
    "في",
    "على",
    "من",
    "الى",
    "إلى",
    "عن",
    "مع",
    "هذا",
    "هذه",
    "ذلك",
    "تلك",
    "كان",
    "كانت",
    "وقد",
    "كما",
    "او",
    "أو",
    "أن",
    "إن",
    "ما",
    "لا",
    "لم",
    "لن",
    "ثم",
}


def remove_stopwords(text: str, stopwords: set[str] | None = None) -> str:
    words = text.split()
    active_stopwords = stopwords or ARABIC_STOPWORDS
    filtered = [w for w in words if w not in active_stopwords]
    return " ".join(filtered)


def clean_arabic_text(text: str, remove_stop_words: bool = True) -> str:
    """Normalize then optionally remove stopwords."""
    cleaned = normalize_arabic_text(
        text,
        normalize_teh_marbuta=False,
        remove_punctuation=True,
    )
    if remove_stop_words:
        cleaned = remove_stopwords(cleaned)
    return cleaned.strip()
