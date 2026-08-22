"""Bidirectional entity search (M5).

An Arabic query matches directly against normalized canonical names --
that's the easy direction, the same normalization used everywhere else in
resolution. An English query has no canonical Arabic form to compare
against, so it has to go through one of two bridges instead:

1. the translation cache (src/store/translations.py) -- if this entity's
   name has already been translated to English, match on that text.
2. romanized candidate spellings (src/lang/arabic.py) -- for names that
   haven't been translated yet, or where the query is a transliteration
   rather than a translation ("al-Sisi" vs "Sisi").

Both bridges are checked and unioned, because a name might be caught by
one and missed by the other (a fresh entity has no cached translation yet;
a name outside _KNOWN_ROMANIZATIONS only romanizes to a rough letter-level
guess that a real person might not have typed).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import Entity
from src.lang import REGISTRY
from src.lang.arabic import ArabicAdapter
from src.store.orm import EntityORM, TranslationORM

_ARABIC = ArabicAdapter()


def search_entities(session: Session, query: str, limit: int = 20) -> list[Entity]:
    """Find entities whose canonical name matches `query`, in either script.

    `query` may be Arabic or English/Latin script; the matching strategy is
    picked from whichever script dominates it (src/lang.REGISTRY.detect).
    """
    query = query.strip()
    if not query or limit <= 0:
        return []

    adapter = REGISTRY.detect(query)
    if adapter.code == "ar":
        return _search_arabic(session, query, limit)
    return _search_latin(session, query, limit)


def _live_entity_rows(session: Session) -> list[EntityORM]:
    return list(
        session.scalars(select(EntityORM).where(EntityORM.retracted.is_(False))).all()
    )


def _search_arabic(session: Session, query: str, limit: int) -> list[Entity]:
    normalized_query = _ARABIC.normalize(query)
    if not normalized_query:
        return []

    matches: list[EntityORM] = []
    for row in _live_entity_rows(session):
        normalized_name = _ARABIC.normalize(row.canonical_name)
        if normalized_query == normalized_name or normalized_query in normalized_name:
            matches.append(row)
            if len(matches) >= limit:
                break

    return [_to_entity(row) for row in matches]


def _search_latin(session: Session, query: str, limit: int) -> list[Entity]:
    normalized_query = query.strip().lower()

    translated_source_names = _translated_arabic_names_matching(session, normalized_query)

    matches: list[EntityORM] = []
    seen_ids: set[int] = set()
    for row in _live_entity_rows(session):
        if row.id in seen_ids:
            continue

        normalized_name = _ARABIC.normalize(row.canonical_name)
        matched = normalized_name in translated_source_names
        if not matched:
            candidates = _ARABIC.romanize(row.canonical_name)
            matched = any(
                normalized_query == candidate or normalized_query in candidate
                for candidate in candidates
            )

        if matched:
            matches.append(row)
            seen_ids.add(row.id)
            if len(matches) >= limit:
                break

    return [_to_entity(row) for row in matches]


def _translated_arabic_names_matching(session: Session, normalized_query: str) -> set[str]:
    """Normalized Arabic source names whose cached English translation
    matches (exactly or as a substring of) the query.
    """
    rows = session.execute(
        select(TranslationORM.source_text, TranslationORM.translated_text).where(
            TranslationORM.target_lang == "en"
        )
    ).all()

    return {
        _ARABIC.normalize(source_text)
        for source_text, translated_text in rows
        if translated_text
        and (
            normalized_query == translated_text.strip().lower()
            or normalized_query in translated_text.strip().lower()
        )
    }


def _to_entity(row: EntityORM) -> Entity:
    return Entity(
        id=row.id,
        object_type=row.object_type,
        canonical_name=row.canonical_name,
        properties=row.properties,
        supersedes_id=row.supersedes_id,
        retracted=row.retracted,
        retracted_reason=row.retracted_reason,
    )
