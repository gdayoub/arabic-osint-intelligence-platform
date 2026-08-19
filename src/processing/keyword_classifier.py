"""Rule-based topic classifier using configurable Arabic keyword dictionaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import SETTINGS
from src.processing.normalize_arabic import normalize_arabic_text

DEFAULT_TOPICS = {
    "Military": ["جيش", "قصف", "صاروخ", "اشتباك", "هجوم"],
    "Politics": ["حكومة", "رئيس", "انتخابات", "برلمان", "وزارة"],
    "Protests": ["احتجاج", "مظاهرة", "إضراب", "متظاهرين"],
    "Economy": ["اقتصاد", "نفط", "تضخم", "استثمار", "تجارة"],
    "Humanitarian": ["لاجئين", "مساعدات", "نازحين", "إغاثة", "مجاعة"],
}


@dataclass(slots=True)
class ClassificationResult:
    topic: str
    score: int  # total keyword occurrences for the winning topic, not distinct keywords
    matched_keywords: dict[str, list[str]]


class KeywordTopicClassifier:
    """Simple explainable classifier for first production baseline.

    Known ceiling, documented rather than papered over: this counts keyword
    occurrences and has no notion of what a word *refers to*. "رئيس"
    (president/chairman) scores for Politics whether it's a head of state or
    the chairman of a football club, so sports coverage that mentions a club
    chairman can still land in Politics. Fixing that properly needs entity
    recognition (knowing الزمالك is a sports organization), which is M3 in
    docs/AGENT_BRIEF.md — not more keywords.
    """

    def __init__(self, topic_keywords: dict[str, list[str]] | None = None):
        self.topic_keywords = topic_keywords or self._load_default_keywords()
        # Normalize each keyword once at construction rather than per call.
        # Kept alongside the original spelling so matched_keywords still
        # reports what a human wrote in the config, not the folded form.
        self._normalized_keywords: dict[str, list[tuple[str, str]]] = {
            topic: [(kw, normalize_arabic_text(kw)) for kw in keywords]
            for topic, keywords in self.topic_keywords.items()
        }

    def _load_default_keywords(self) -> dict[str, list[str]]:
        path = Path(SETTINGS.topic_keywords_path)
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return DEFAULT_TOPICS

    def classify(self, text: str) -> ClassificationResult:
        # Normalization applies to the comparison copy only — the caller's
        # text is never modified and nothing normalized is ever stored (the
        # M2 principle: fold for comparison, keep originals for offsets).
        # Without this, a keyword written "إضراب" silently fails to match an
        # article that spells it "اضراب".
        normalized = normalize_arabic_text(text or "")
        scores: dict[str, int] = {}
        matches: dict[str, list[str]] = {}

        for topic, keyword_pairs in self._normalized_keywords.items():
            topic_hits: list[str] = []
            total = 0
            for original, normalized_keyword in keyword_pairs:
                occurrences = normalized.count(normalized_keyword)
                if occurrences:
                    topic_hits.append(original)
                    total += occurrences
            matches[topic] = topic_hits
            # Occurrences, not distinct keywords: an article that says
            # "مباراة" six times is more clearly about sport than one that
            # mentions two different political words once each.
            scores[topic] = total

        best_topic = max(scores, key=scores.get) if scores else "Uncategorized"
        best_score = scores.get(best_topic, 0)

        if best_score == 0:
            return ClassificationResult(
                topic="Uncategorized",
                score=0,
                matched_keywords=matches,
            )

        return ClassificationResult(
            topic=best_topic,
            score=best_score,
            matched_keywords=matches,
        )
