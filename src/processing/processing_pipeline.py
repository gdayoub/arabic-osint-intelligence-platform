"""Article-level NLP processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from src.config.settings import SETTINGS
from src.database.models import RawArticle
from src.processing.ai_summarizer import generate_summary
from src.processing.clean_text import clean_arabic_text
from src.processing.escalation_scoring import score_escalation
from src.processing.keyword_classifier import KeywordTopicClassifier

COUNTRY_KEYWORDS = {
    "سوريا": "Syria",
    "العراق": "Iraq",
    "اليمن": "Yemen",
    "لبنان": "Lebanon",
    "فلسطين": "Palestine",
    "غزة": "Gaza",
    "اسرائيل": "Israel",
    "إسرائيل": "Israel",
    "مصر": "Egypt",
    "ليبيا": "Libya",
    "السودان": "Sudan",
    "الأردن": "Jordan",
}


@dataclass(slots=True)
class ProcessedOutput:
    cleaned_text: str
    topic: str
    sentiment_or_escalation: str
    country_guess: str | None
    keyword_matches: dict
    ai_summary: str | None = None


class ArticleProcessingPipeline:
    """Transforms raw articles into analytically useful processed records."""

    def __init__(self) -> None:
        self.classifier = KeywordTopicClassifier()

    def guess_country(self, text: str) -> str | None:
        for keyword, country in COUNTRY_KEYWORDS.items():
            if keyword in text:
                return country
        return None

    def process(self, article: RawArticle) -> ProcessedOutput:
        cleaned = clean_arabic_text(
            article.body,
            remove_stop_words=SETTINGS.remove_stopwords_default,
        )
        cls = self.classifier.classify(cleaned)
        escalation = score_escalation(cleaned)

        ai_summary = generate_summary(
            title=article.title or "",
            cleaned_text=cleaned,
            escalation=escalation.label,
        )

        return ProcessedOutput(
            cleaned_text=cleaned,
            topic=cls.topic,
            sentiment_or_escalation=escalation.label,
            country_guess=self.guess_country(cleaned),
            keyword_matches={
                "topic_matches": cls.matched_keywords,
                "escalation_matches": escalation.matches,
                "topic_score": cls.score,
                "escalation_score": escalation.score,
            },
            ai_summary=ai_summary,
        )
