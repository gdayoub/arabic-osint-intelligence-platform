"""Arabic text normalization utilities.

These transformations are designed for news analytics, where we want to reduce
orthographic variation without destroying semantic signal.
"""

from __future__ import annotations

import re

# Arabic diacritics and Quranic marks (tashkeel).
TASHKEEL_RE = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]"
)

# Tatweel (kashida) used for visual stretch.
TATWEEL_RE = re.compile(r"\u0640")

# Arabic + Latin punctuation.
PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06FF]")

MULTISPACE_RE = re.compile(r"\s+")


def remove_tashkeel(text: str) -> str:
    """Remove diacritics to unify lexical forms.

    Example: "مُظَاهَرَة" -> "مظاهرة"
    """
    return TASHKEEL_RE.sub("", text)


def remove_tatweel(text: str) -> str:
    """Remove elongation character often used for styling."""
    return TATWEEL_RE.sub("", text)


def normalize_alef_variants(text: str) -> str:
    """Map Alef variants to bare Alef for token consistency."""
    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def normalize_ya_teh_marbuta(text: str, normalize_teh_marbuta: bool = True) -> str:
    """Normalize Ya/Alef Maqsura and optionally Ta Marbuta.

    - Alef Maqsura (ى) -> Ya (ي) improves lexical consistency.
    - Ta Marbuta (ة) can optionally map to Heh (ه) for aggressive normalization.
    """
    text = text.replace("ى", "ي")
    if normalize_teh_marbuta:
        text = text.replace("ة", "ه")
    return text


def strip_punctuation(text: str) -> str:
    """Remove punctuation while preserving Arabic letters, digits, and whitespace."""
    return PUNCT_RE.sub(" ", text)


def collapse_whitespace(text: str) -> str:
    return MULTISPACE_RE.sub(" ", text).strip()


def normalize_arabic_text(
    text: str,
    normalize_teh_marbuta: bool = False,
    remove_punctuation: bool = True,
) -> str:
    """Apply full Arabic normalization pipeline."""
    if not text:
        return ""

    normalized = text
    normalized = remove_tashkeel(normalized)
    normalized = remove_tatweel(normalized)
    normalized = normalize_alef_variants(normalized)
    normalized = normalize_ya_teh_marbuta(
        normalized, normalize_teh_marbuta=normalize_teh_marbuta
    )

    if remove_punctuation:
        normalized = strip_punctuation(normalized)

    normalized = collapse_whitespace(normalized)
    return normalized
