"""Rule-based topic classifier using configurable Arabic keyword dictionaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import SETTINGS

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
    score: int
    matched_keywords: dict[str, list[str]]


class KeywordTopicClassifier:
    """Simple explainable classifier for first production baseline."""

    def __init__(self, topic_keywords: dict[str, list[str]] | None = None):
        self.topic_keywords = topic_keywords or self._load_default_keywords()

    def _load_default_keywords(self) -> dict[str, list[str]]:
        path = Path(SETTINGS.topic_keywords_path)
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return DEFAULT_TOPICS

    def classify(self, text: str) -> ClassificationResult:
        normalized = text or ""
        scores: dict[str, int] = {}
        matches: dict[str, list[str]] = {}

        for topic, keywords in self.topic_keywords.items():
            topic_hits = [kw for kw in keywords if kw in normalized]
            matches[topic] = topic_hits
            scores[topic] = len(topic_hits)

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
