"""Read/write layer for the translation cache (M5).

Sits between the pipeline and `TranslationORM`. The pipeline asks for
translations of a list of strings; this decides which are already cached,
sends only the misses to the translator, and persists the results.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import ExtractorVersion
from src.processing.translation import SOURCE_LANG, TARGET_LANG, Translator, batched, source_sha256
from src.store.orm import TranslationORM

logger = logging.getLogger("store.translations")


def get_cached(session: Session, texts: list[str], target_lang: str = TARGET_LANG) -> dict[str, str]:
    """Map source text → translation, for whichever of `texts` are cached."""
    if not texts:
        return {}

    hashes = {source_sha256(text) for text in texts}
    rows = session.scalars(
        select(TranslationORM).where(
            TranslationORM.source_sha256.in_(hashes),
            TranslationORM.target_lang == target_lang,
        )
    ).all()
    return {row.source_text: row.translated_text for row in rows}


def translate_texts(
    session: Session,
    texts: list[str],
    translator: Translator,
    extractor_version: ExtractorVersion,
    max_new: int | None = None,
    target_lang: str = TARGET_LANG,
) -> dict[str, str]:
    """Translate `texts`, using the cache and only paying for misses.

    `max_new` caps how many *uncached* strings get sent in one run — a
    guard against a single run burning a monthly quota (DeepL free is 500k
    characters/month). Anything over the cap is simply left untranslated
    and picked up next run, which is fine because the dashboard renders
    Arabic with or without a translation.
    """
    unique_texts = list(dict.fromkeys(text for text in texts if text and text.strip()))
    if not unique_texts:
        return {}

    cached = get_cached(session, unique_texts, target_lang=target_lang)
    missing = [text for text in unique_texts if text not in cached]

    if max_new is not None and len(missing) > max_new:
        logger.info("Capping translation at %d of %d missing strings", max_new, len(missing))
        missing = missing[:max_new]

    for batch in batched(missing):
        try:
            translated = translator.translate(batch)
        except Exception:
            # A failed batch is not fatal: those strings stay uncached and
            # get retried next run. Losing a translation is cosmetic; losing
            # the whole pipeline run over it would not be.
            logger.exception("Translation batch failed (%d strings); leaving uncached", len(batch))
            continue

        for source_text, translated_text in zip(batch, translated):
            session.add(
                TranslationORM(
                    source_sha256=source_sha256(source_text),
                    source_lang=SOURCE_LANG,
                    target_lang=target_lang,
                    source_text=source_text,
                    translated_text=translated_text,
                    extractor_version_id=extractor_version.id,
                )
            )
            cached[source_text] = translated_text
        session.flush()

    return cached
