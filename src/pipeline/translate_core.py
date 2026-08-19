"""Translates document titles Arabic → English and caches the results (M5).

Runs as its own pipeline step, after process-core. Deliberately separate
from processing rather than folded into it, and deliberately capped:

  * The API call is the only network I/O in the classification path. Keeping
    it in its own step means a DeepL outage or a spent quota degrades the
    dashboard (missing English titles) instead of failing the whole run.
  * Calls happen outside any long-lived write transaction. The legacy
    ai_summarizer made hundreds of serial API calls inside one open session
    — the exact defect docs/adr/0011 refused to carry forward.

Only titles are translated for now. Bodies would be ~50x the character
volume against a 500k/month free quota, and the dashboard only ever shows
titles. Evidence-sentence translation belongs with M5's entity work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from src.config.settings import SETTINGS
from src.processing.translation import EXTRACTOR_NAME, EXTRACTOR_VERSION, Translator, get_translator
from src.store.database import get_core_session
from src.store.orm import DocumentORM, FactORM
from src.store.provenance import register_extractor_version
from src.store.translations import translate_texts

logger = logging.getLogger("pipeline.translate_core")


@dataclass(slots=True)
class TranslateCoreStats:
    titles_seen: int = 0
    already_cached: int = 0
    newly_translated: int = 0


def _recent_title_values(session, limit: int) -> list[str]:
    """Titles of the most recent non-retracted documents.

    Scoped to recent documents rather than the whole corpus because the
    dashboard only renders a recent slice — translating a year of archive
    would spend quota on text nothing displays.
    """
    rows = session.execute(
        select(FactORM.payload)
        .join(DocumentORM, DocumentORM.id == FactORM.subject_id)
        .where(
            FactORM.subject_table == "documents",
            FactORM.fact_type == "title",
            FactORM.retracted.is_(False),
            DocumentORM.retracted.is_(False),
        )
        .order_by(DocumentORM.collected_at.desc())
        .limit(limit)
    ).all()

    titles: list[str] = []
    for (payload,) in rows:
        value = (payload or {}).get("value")
        if isinstance(value, str) and value.strip():
            titles.append(value)
    return titles


def run_core_translation(
    document_limit: int = 200,
    max_new: int | None = None,
    translator: Translator | None = None,
) -> TranslateCoreStats:
    max_new = max_new if max_new is not None else SETTINGS.max_translations_per_run
    translator = translator or get_translator()

    stats = TranslateCoreStats()
    with get_core_session() as session:
        extractor = register_extractor_version(
            session,
            EXTRACTOR_NAME,
            EXTRACTOR_VERSION,
            description="DeepL API, Arabic to English, document titles",
        )

        titles = _recent_title_values(session, limit=document_limit)
        stats.titles_seen = len(titles)

        before = len(translate_texts(session, titles, translator, extractor, max_new=0))
        stats.already_cached = before

        translated = translate_texts(session, titles, translator, extractor, max_new=max_new)
        stats.newly_translated = len(translated) - before

    return stats
