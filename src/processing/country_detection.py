"""Rule-based country detection over Arabic text.

Replaces the legacy `ArticleProcessingPipeline.guess_country`, which returned
the *first* country whose keyword appeared anywhere in the text, in Python
dict order. That produced visibly wrong labels: an article about Egypt's
Dabaa nuclear plant that mentioned Syria once in passing was tagged Syria,
because "سوريا" happened to come first in the dict.

This version counts every match and returns the most-mentioned country.

Deliberately still a keyword gazetteer, not real toponym resolution. It
cannot disambiguate places that share a name, weight a headline mention
above a passing one, or resolve "the capital" to a city. M7 (GeoNames +
PostGIS + context-based disambiguation) is what actually solves that; this
is a stopgap that just needs to stop being *wrong* in the obvious cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from src.processing.normalize_arabic import normalize_arabic_text

# Keywords are stored WITHOUT the definite article "ال" — the matching
# pattern adds it as optional, so "اردن" covers الأردن / بالأردن / أردن.
# Spelling variants of alef/hamza don't need listing either; both the text
# and these keywords get normalized before matching, which collapses
# أ/إ/آ → ا (so "إسرائيل" and "اسرائيل" converge on one form).
COUNTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Syria": ("سوريا", "سورية", "دمشق", "حلب", "ادلب"),
    "Iraq": ("عراق", "بغداد", "الموصل", "اربيل"),
    "Yemen": ("يمن", "صنعاء", "عدن", "حوثي"),
    "Lebanon": ("لبنان", "بيروت", "حزب الله"),
    "Palestine": ("فلسطين", "الضفة الغربية", "رام الله", "القدس"),
    "Gaza": ("غزة", "رفح", "خان يونس"),
    "Israel": ("اسرائيل", "تل ابيب"),
    "Egypt": ("مصر", "القاهرة"),
    "Libya": ("ليبيا", "طرابلس", "بنغازي"),
    "Sudan": ("سودان", "الخرطوم", "دارفور"),
    "Jordan": ("اردن",),  # عمّان omitted: after diacritic stripping it collides with عُمان (Oman)
    "Saudi Arabia": ("سعودية", "الرياض", "جدة", "مكة"),
    "Iran": ("ايران", "طهران"),
    "Turkey": ("تركيا", "انقرة", "اسطنبول"),
    "UAE": ("امارات", "دبي", "ابوظبي"),
    "Qatar": ("قطر", "الدوحة"),
    "Kuwait": ("كويت",),
    "Bahrain": ("بحرين",),
    "Oman": ("سلطنة عمان", "مسقط"),
    "Tunisia": ("تونس",),
    "Algeria": ("جزائر",),
    "Morocco": ("مغرب", "الرباط"),
    "Somalia": ("صومال", "مقديشو"),
    "Afghanistan": ("افغانستان", "كابول"),
    "Pakistan": ("باكستان", "اسلام اباد"),
    "United States": ("امريكا", "الولايات المتحدة", "واشنطن"),
    "Russia": ("روسيا", "موسكو"),
    "Ukraine": ("اوكرانيا", "كييف"),
    "China": ("صين", "بكين"),
    "France": ("فرنسا", "باريس"),
    "United Kingdom": ("بريطانيا", "لندن"),
    "Germany": ("المانيا", "برلين"),
}

_ARABIC_RANGE = r"؀-ۿ"

# Arabic attaches conjunctions and prepositions directly to the word
# (و/ف/ب/ل/ك), optionally followed by the definite article. Matching a bare
# substring would count "مصر" inside "مصرف" (bank) as a mention of Egypt, so
# the pattern requires no Arabic letter immediately before the prefix or
# immediately after the keyword.
_PREFIX = r"[وفبلك]{0,2}(?:ال)?"


@dataclass(frozen=True, slots=True)
class CountryDetection:
    """The winning country plus the full tally, so callers can show
    secondary countries or explain *why* a document was labelled."""

    country: str | None
    counts: dict[str, int]


@lru_cache(maxsize=1)
def _compiled_keywords() -> tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]:
    """Build one regex per keyword, once per process.

    lru_cache(maxsize=1) on a no-argument function is the boring way to get
    lazy module-level initialization: the work happens on first call, not at
    import time, and every later call reuses the cached result.
    """
    compiled: list[tuple[str, tuple[re.Pattern[str], ...]]] = []
    for country, keywords in COUNTRY_KEYWORDS.items():
        patterns = tuple(
            re.compile(rf"(?<![{_ARABIC_RANGE}]){_PREFIX}{re.escape(normalize_arabic_text(kw))}(?![{_ARABIC_RANGE}])")
            for kw in keywords
        )
        compiled.append((country, patterns))
    return tuple(compiled)


def detect_country(text: str) -> CountryDetection:
    """Count keyword matches per country; the most-mentioned one wins.

    Normalization is applied to the comparison text only — never written
    back to storage. That's the M2 principle the brief spells out: stored
    text stays exactly as scraped so mention offsets remain valid (P2), and
    normalization exists purely to make comparison keys line up.
    """
    if not text:
        return CountryDetection(country=None, counts={})

    normalized = normalize_arabic_text(text)

    counts: dict[str, int] = {}
    for country, patterns in _compiled_keywords():
        total = sum(len(pattern.findall(normalized)) for pattern in patterns)
        if total:
            counts[country] = total

    if not counts:
        return CountryDetection(country=None, counts={})

    # max() over items picks the highest count; ties fall to whichever the
    # iterator reached first, which follows COUNTRY_KEYWORDS order. Ties are
    # genuinely ambiguous here, so an arbitrary-but-stable winner is fine.
    winner = max(counts.items(), key=lambda item: item[1])[0]
    return CountryDetection(country=winner, counts=counts)
