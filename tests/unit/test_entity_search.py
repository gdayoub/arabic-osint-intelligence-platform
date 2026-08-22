"""Tests for bidirectional entity search (M5).

Entities are inserted directly via EntityORM rather than through
create_entity/create_mention -- search only reads entities.canonical_name
and doesn't care about the provenance chain that produced them, so building
a full document/mention/extractor-version chain per test would just be
noise.
"""

from __future__ import annotations

from src.search.entity_search import search_entities
from src.store.orm import EntityORM, ExtractorVersionORM, TranslationORM


def _add_entity(session, canonical_name: str, object_type: str = "person") -> EntityORM:
    row = EntityORM(object_type=object_type, canonical_name=canonical_name, properties={})
    session.add(row)
    session.flush()
    return row


def test_arabic_query_matches_normalized_canonical_name(session):
    _add_entity(session, "بشار الأسد")
    session.commit()

    # diacritics present in the query but not in the stored name -- normalize
    # must fold both sides the same way for this to match.
    results = search_entities(session, "بشار الاسد")

    assert len(results) == 1
    assert results[0].canonical_name == "بشار الأسد"


def test_arabic_query_finds_no_unrelated_entity(session):
    _add_entity(session, "بشار الأسد")
    session.commit()

    assert search_entities(session, "دونالد ترامب") == []


def test_english_query_matches_known_name_romanization(session):
    _add_entity(session, "محمد بن سلمان")
    session.commit()

    # "محمد" is in the known-name lookup and romanizes to "mohammed" among
    # other candidates; "بن سلمان" has no lookup entry so it romanizes to a
    # rough vowel-less guess -- querying on the known part is the honest
    # test of the romanization bridge.
    results = search_entities(session, "mohammed")

    assert any(r.canonical_name == "محمد بن سلمان" for r in results)


def test_english_query_matches_cached_translation(session):
    entity = _add_entity(session, "الجزيرة")
    extractor_version = ExtractorVersionORM(name="deepl_translator", version="1.0.0")
    session.add(extractor_version)
    session.flush()
    session.add(
        TranslationORM(
            source_sha256="x" * 64,
            source_lang="ar",
            target_lang="en",
            source_text="الجزيرة",
            translated_text="Al Jazeera",
            extractor_version_id=extractor_version.id,
        )
    )
    session.commit()

    results = search_entities(session, "Al Jazeera")

    assert any(r.id == entity.id for r in results)


def test_english_query_finds_no_unrelated_entity(session):
    _add_entity(session, "محمد بن سلمان")
    session.commit()

    assert search_entities(session, "vladimir putin") == []


def test_retracted_entities_are_excluded(session):
    row = _add_entity(session, "بشار الأسد")
    row.retracted = True
    session.commit()

    assert search_entities(session, "بشار الأسد") == []


def test_retracted_entities_are_excluded_from_english_query(session):
    row = _add_entity(session, "محمد بن سلمان")
    row.retracted = True
    session.commit()

    assert search_entities(session, "mohammed") == []


def test_empty_query_returns_nothing(session):
    _add_entity(session, "بشار الأسد")
    session.commit()

    assert search_entities(session, "") == []
    assert search_entities(session, "   ") == []


def test_limit_is_respected(session):
    for i in range(5):
        _add_entity(session, f"بشار الأسد {i}")
    session.commit()

    results = search_entities(session, "بشار", limit=2)

    assert len(results) == 2
