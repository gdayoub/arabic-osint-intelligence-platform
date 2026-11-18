"""Real archive regressions: truncation, missing geography, stale entities, publication."""
import json
from datetime import datetime, timezone
import pytest
from scripts.bake_atlas import bake_atlas
from scripts.bake_dashboard_data import bake, bake_country_pages, publish_snapshot_bundle
from src.contracts.atlas import AtlasSnapshot
from src.pipeline.extract_core import EXTRACTION_MARKER
from src.store.orm import DocumentORM, EntityORM
from src.store.provenance import register_extractor_version, create_mention, create_entity, record_document_fact
from tests.unit.test_bake import _seed_document

STAMP = "2026-09-07T12:00:00+00:00"


def seed(session, ontology, blob_store, number, country="Egypt"):
    extractor = register_extractor_version(session, "test", "1.0.0")
    return _seed_document(session, ontology, blob_store, extractor,
                          url=f"https://example.com/{number}", body="ذكر محمد أحمد اليوم في القاهرة.",
                          title=f"خبر {number}", topic="Politics", escalation="high", country=country)


def test_archive_includes_beyond_country_limit_and_unassigned(session, ontology, blob_store):
    for index in range(55):
        seed(session, ontology, blob_store, index)
    seed(session, ontology, blob_store, 55, country=None)
    session.commit()
    assert len(bake_country_pages(session)["Egypt"]["articles"]) == 50
    archive = bake_atlas(session, STAMP)
    assert len(archive["articles"]) == 56
    assert len([a for a in archive["articles"] if a["country"] is None]) == 1
    assert "ذكر محمد أحمد اليوم" not in json.dumps(archive, ensure_ascii=False)


def test_archive_excludes_retracted_documents_and_entities(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    dead = seed(session, ontology, blob_store, 2)
    session.get(DocumentORM, dead.id).retracted = True
    extractor = register_extractor_version(session, "gazetteer_test", "1.0.0")
    mention = create_mention(session, document, "محمد أحمد", 4, 13, "person", extractor, ontology, language="ar")
    record_document_fact(session, document, EXTRACTION_MARKER, 1, extractor, ontology)
    entity = create_entity(session, "person", "محمد أحمد", {}, mention, extractor, ontology)
    session.flush()
    archive = bake_atlas(session, STAMP)
    assert len(archive["articles"]) == 1
    assert archive["articles"][0]["entity_ids"] == [str(entity.id)]
    assert archive["entities"][0]["romanizations"]
    session.get(EntityORM, entity.id).retracted = True
    archive = bake_atlas(session, STAMP)
    assert archive["entities"] == []
    assert archive["articles"][0]["entity_ids"] == []


def test_publication_and_collection_dates_remain_distinct(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    row = session.get(DocumentORM, document.id)
    row.published_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    row.collected_at = datetime(2026, 9, 7, tzinfo=timezone.utc)
    session.flush()
    article = bake_atlas(session, STAMP)["articles"][0]
    assert article["published_date"].startswith("2026-08-01")
    assert article["collected_at"].startswith("2026-09-07")


def test_atlas_is_published_in_same_atomic_directory(session, ontology, blob_store, tmp_path):
    seed(session, ontology, blob_store, 1)
    session.commit()
    dashboard = bake(session, generated_at=STAMP)
    pages = bake_country_pages(session, generated_at=STAMP)
    dashboard["countries"] = [{"country": "Egypt", "slug": "egypt", "count": 1}]
    atlas = bake_atlas(session, STAMP)
    path = tmp_path / "data.json"
    publish_snapshot_bundle(dashboard, pages, path, atlas=atlas)
    before = (tmp_path / "countries/_atlas.json").read_bytes()
    bad = {**atlas, "generated_at": "2026-09-08T12:00:00+00:00"}
    with pytest.raises(ValueError, match="same snapshot"):
        publish_snapshot_bundle(dashboard, pages, path, atlas=bad)
    assert (tmp_path / "countries/_atlas.json").read_bytes() == before


def test_archive_rejects_orphan_entity_and_unsafe_source():
    payload = {"atlas_version": 1, "generated_at": STAMP, "complete": True,
               "classification": {}, "entities": [], "articles": [{"id": "1", "title": "A", "title_en": None,
               "source": "Example", "url": "https://example.com/a", "topic": None, "escalation": None,
               "country": None, "published_date": None, "collected_at": None, "entity_ids": ["missing"]}]}
    with pytest.raises(ValueError, match="absent entity"):
        AtlasSnapshot.model_validate(payload)
    payload["articles"][0]["entity_ids"] = []
    payload["articles"][0]["url"] = "javascript:alert(1)"
    with pytest.raises(ValueError):
        AtlasSnapshot.model_validate(payload)
