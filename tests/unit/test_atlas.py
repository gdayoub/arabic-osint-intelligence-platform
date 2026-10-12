"""Archive regressions against the existing ORM and rollback-safe staging."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import scripts.bake_dashboard_data as bake_module
from scripts.bake_atlas import _require_snapshot, archive_read_session, bake_atlas
from scripts.bake_dashboard_data import bake, bake_country_pages, publish_snapshot_bundle
from src.contracts.atlas import atlas_snapshot_id
from src.pipeline.extract_core import EXTRACTION_MARKER
from src.pipeline.process_core import EXTRACTOR_NAME, EXTRACTOR_VERSION
from src.processing.translation import source_sha256
from src.store.orm import CoreBase, DocumentORM, EntityORM, MentionORM, TranslationORM
from src.store.provenance import (
    add_entity_evidence,
    create_document,
    create_entity,
    create_mention,
    record_document_fact,
    register_extractor_version,
)
from tests.unit.test_bake import _seed_document

STAMP = "2026-09-07T12:00:00+00:00"
BODY = "ذكر محمد أحمد اليوم في القاهرة. هذه تفاصيل جسم المقال الخاصة."


def seed(session, ontology, blob_store, number, country="Egypt"):
    extractor = register_extractor_version(session, "archive_test_metadata", "1.0.0")
    return _seed_document(
        session, ontology, blob_store, extractor,
        url=f"https://example.com/{number}", body=BODY,
        title=f"خبر {number}", topic="Politics", escalation="high", country=country,
    )


def entity_evidence(session, ontology, document, *, name="محمد أحمد", family="gazetteer_test", version="1.0.0"):
    extractor = register_extractor_version(session, family, version)
    mention = create_mention(session, document, "محمد أحمد", 4, 13, "person", extractor, ontology, language="ar")
    record_document_fact(session, document, EXTRACTION_MARKER, 1, extractor, ontology)
    entity = create_entity(session, "person", name, {"surface_forms": ["a stale alias"]}, mention, extractor, ontology)
    session.flush()
    return mention, entity, extractor


def bundle(session):
    session.flush()
    data = bake(session, generated_at=STAMP)
    pages = bake_country_pages(session, generated_at=STAMP)
    data["countries"] = [
        {"country": page["country"], "slug": page["slug"], "count": page["total"]}
        for page in pages.values()
    ]
    return data, pages, bake_atlas(session, STAMP)


def test_archive_includes_beyond_country_limit_unassigned_and_undated(session, ontology, blob_store):
    for index in range(55):
        seed(session, ontology, blob_store, index)
    unassigned = seed(session, ontology, blob_store, 55, country=None)
    session.flush()
    data, pages, archive = bundle(session)
    assert len(pages["Egypt"]["articles"]) == 50
    assert len(archive["articles"]) == data["stats"]["total_processed"] == 56
    row = next(row for row in archive["articles"] if row["id"] == str(unassigned.id))
    assert row["country"] is None
    assert row["published_date"] is None
    assert row["entity_ids"] == []
    assert "هذه تفاصيل جسم المقال الخاصة" not in json.dumps(archive, ensure_ascii=False)
    assert archive["scope"] == "current_processed_live_documents"
    assert archive["complete"] is True


def test_archive_excludes_retracted_unprocessed_stale_and_mixed_generations(session, ontology, blob_store):
    current = seed(session, ontology, blob_store, "current")
    dead = seed(session, ontology, blob_store, "dead")
    session.get(DocumentORM, dead.id).retracted = True
    unprocessed = create_document(session, source="test", text=BODY, content_hash="unprocessed", blob_store=blob_store)
    metadata = register_extractor_version(session, "archive_test_metadata", "1.0.0")
    record_document_fact(session, unprocessed, "title", "Unprocessed", metadata, ontology)
    old_processor = register_extractor_version(session, EXTRACTOR_NAME, "0.9.0")
    for suffix, stale_types in (("mixed", ["escalation"]), ("old", ["topic", "escalation"])):
        document = seed(session, ontology, blob_store, suffix)
        for fact_type in stale_types:
            record_document_fact(session, document, fact_type, "stale", old_processor, ontology)
    session.flush()
    archive = bake_atlas(session, STAMP)
    assert [row["id"] for row in archive["articles"]] == [str(current.id)]
    assert archive["classification"] == {"name": EXTRACTOR_NAME, "version": EXTRACTOR_VERSION}


def test_publication_and_collection_dates_are_distinct(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    row = session.get(DocumentORM, document.id)
    row.published_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    row.collected_at = datetime(2026, 9, 7, tzinfo=timezone.utc)
    session.flush()
    article = bake_atlas(session, STAMP)["articles"][0]
    assert article["published_date"].startswith("2026-08-01")
    assert article["collected_at"].startswith("2026-09-07")


def test_current_membership_excludes_historical_aliases_and_retractions(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    mention, entity, _ = entity_evidence(session, ontology, document)
    archive = bake_atlas(session, STAMP)
    assert archive["articles"][0]["entity_ids"] == [str(entity.id)]
    assert archive["entities"][0]["surface_forms"] == ["محمد أحمد"]
    assert archive["entities"][0]["romanizations"]
    assert "a stale alias" not in json.dumps(archive)
    session.get(MentionORM, mention.id).retracted = True
    session.flush()
    archive = bake_atlas(session, STAMP)
    assert archive["entities"] == []
    assert archive["articles"][0]["entity_ids"] == []


def test_obsolete_extraction_and_retracted_entities_do_not_support_archive(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    _, old, _ = entity_evidence(session, ontology, document, version="1.0.0")
    _, current, _ = entity_evidence(session, ontology, document, version="2.0.0")
    archive = bake_atlas(session, STAMP)
    assert [row["id"] for row in archive["entities"]] == [str(current.id)]
    assert str(old.id) not in archive["articles"][0]["entity_ids"]
    session.get(EntityORM, current.id).retracted = True
    session.flush()
    assert bake_atlas(session, STAMP)["entities"] == []


def test_other_extraction_families_coexist_and_unprocessed_evidence_is_excluded(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    _, first, _ = entity_evidence(session, ontology, document, family="first")
    _, second, _ = entity_evidence(session, ontology, document, family="second")
    unprocessed = create_document(session, source="test", text=BODY, content_hash="unprocessed", blob_store=blob_store)
    _, excluded, _ = entity_evidence(session, ontology, unprocessed)
    archive = bake_atlas(session, STAMP)
    assert {row["id"] for row in archive["entities"]} == {str(first.id), str(second.id)}
    assert str(excluded.id) not in archive["articles"][0]["entity_ids"]


def test_retracted_document_evidence_cannot_keep_an_entity_current(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    entity_evidence(session, ontology, document)
    session.get(DocumentORM, document.id).retracted = True
    session.flush()
    archive = bake_atlas(session, STAMP)
    assert archive["articles"] == archive["entities"] == []


def test_ambiguous_live_entity_membership_fails_closed(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    mention, _, extractor = entity_evidence(session, ontology, document)
    create_entity(session, "person", "Other", {}, mention, extractor, ontology)
    session.flush()
    with pytest.raises(ValueError, match="multiple live entities"):
        bake_atlas(session, STAMP)


def test_conflicting_membership_object_type_fails_closed(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    _, entity, _ = entity_evidence(session, ontology, document)
    session.get(EntityORM, entity.id).object_type = "organization"
    session.flush()
    with pytest.raises(ValueError, match="conflicting object type"):
        bake_atlas(session, STAMP)


def test_repeated_mentions_deduplicate_article_memberships(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    _, entity, extractor = entity_evidence(session, ontology, document)
    second = create_mention(session, document, "محمد", 4, 8, "person", extractor, ontology, language="ar")
    add_entity_evidence(session, entity, second, extractor)
    session.flush()
    archive = bake_atlas(session, STAMP)
    assert archive["articles"][0]["entity_ids"] == [str(entity.id)]
    assert set(archive["entities"][0]["surface_forms"]) == {"محمد أحمد", "محمد"}


def test_only_existing_translation_cache_is_read(session, ontology, blob_store, monkeypatch):
    document = seed(session, ontology, blob_store, 1)
    _, entity, extractor = entity_evidence(session, ontology, document)
    for source, translated in (("خبر 1", "News 1"), ("محمد أحمد", "Mohammed Ahmed")):
        session.add(TranslationORM(
            source_sha256=source_sha256(source), source_lang="AR", target_lang="EN-US",
            source_text=source, translated_text=translated, extractor_version_id=extractor.id,
        ))
    session.flush()
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: pytest.fail("archive must never translate over network"))
    archive = bake_atlas(session, STAMP)
    assert archive["articles"][0]["title_en"] == "News 1"
    assert archive["entities"][0]["name_en"] == "Mohammed Ahmed"
    assert archive["entities"][0]["id"] == str(entity.id)


def test_snapshot_guard_rejects_pending_writes_and_read_committed(session, ontology, blob_store):
    document = seed(session, ontology, blob_store, 1)
    session.get(DocumentORM, document.id).source = "Pending change"
    with pytest.raises(ValueError, match="pending writes"):
        bake_atlas(session, STAMP)
    fake = Mock(new=set(), dirty=set(), deleted=set())
    fake.connection.return_value.dialect.name = "postgresql"
    fake.connection.return_value.get_isolation_level.return_value = "READ COMMITTED"
    with pytest.raises(ValueError, match="repeatable-read"):
        _require_snapshot(fake)


def test_sqlite_snapshot_stays_consistent_during_concurrent_commit(tmp_path, ontology, blob_store):
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshot.sqlite'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    CoreBase.metadata.create_all(engine)
    with Session(engine) as writer:
        original = seed(writer, ontology, blob_store, 1)
        original_id = original.id
        writer.commit()
    with archive_read_session(engine) as reader:
        assert reader.scalar(select(func.count()).select_from(DocumentORM)) == 1
        with Session(engine) as writer:
            seed(writer, ontology, blob_store, 2)
            writer.commit()
        archive = bake_atlas(reader, STAMP)
        assert [row["id"] for row in archive["articles"]] == [str(original_id)]
    with archive_read_session(engine) as reader:
        assert len(bake_atlas(reader, STAMP)["articles"]) == 2
    engine.dispose()


def test_archive_bundle_stages_together_and_rejects_cross_snapshot(session, ontology, blob_store, tmp_path):
    seed(session, ontology, blob_store, 1)
    data, pages, archive = bundle(session)
    out = tmp_path / "data.json"
    publish_snapshot_bundle(data, pages, out, atlas=archive)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*.json")}
    archive["generated_at"] = "2026-09-08T12:00:00+00:00"
    archive["snapshot_id"] = atlas_snapshot_id(archive)
    with pytest.raises(ValueError, match="same snapshot"):
        publish_snapshot_bundle(data, pages, out, atlas=archive)
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("failure", ["stage_archive", "promote_main"])
def test_failed_archive_stage_or_promotion_preserves_prior_bundle(session, ontology, blob_store, tmp_path, monkeypatch, failure):
    seed(session, ontology, blob_store, 1)
    data, pages, archive = bundle(session)
    out = tmp_path / "data.json"
    (tmp_path / "countries").mkdir()
    out.write_text("old dashboard")
    (tmp_path / "countries/egypt.json").write_text("old country")
    (tmp_path / "countries/_atlas.json").write_text("old archive")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*.json")}
    write = bake_module.write_data_json
    replace = os.replace

    def fail_write(payload, path):
        if path.name == "_atlas.json":
            raise OSError("archive stage failed")
        return write(payload, path)

    def fail_replace(source, destination):
        source = Path(source)
        if source.name == "data.json" and source.parent.name.startswith(".dashboard-snapshot-stage-"):
            raise OSError("main promotion failed")
        return replace(source, destination)

    if failure == "stage_archive":
        monkeypatch.setattr(bake_module, "write_data_json", fail_write)
    else:
        monkeypatch.setattr(bake_module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        publish_snapshot_bundle(data, pages, out, atlas=archive)
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*.json")}


def test_run_bake_archive_is_explicit_opt_in(session, ontology, blob_store, tmp_path, monkeypatch):
    seed(session, ontology, blob_store, 1)
    session.commit()
    monkeypatch.setattr("src.store.database.get_core_engine", lambda: session.get_bind())
    out = tmp_path / "data.json"
    data = bake_module.run_bake(out, include_atlas=True)
    archive = json.loads((tmp_path / "countries/_atlas.json").read_text())
    assert len(archive["articles"]) == data["stats"]["total_processed"] == 1
    assert archive["generated_at"] == data["generated_at"]


def test_existing_run_bake_default_does_not_activate_archive(session, ontology, blob_store, tmp_path, monkeypatch):
    from contextlib import contextmanager

    seed(session, ontology, blob_store, 1)
    session.commit()

    @contextmanager
    def fixture_session():
        yield session

    monkeypatch.setattr(bake_module, "get_core_session", fixture_session)
    bake_module.run_bake(tmp_path / "data.json")
    assert not (tmp_path / "countries/_atlas.json").exists()


def test_existing_release_collector_includes_archive_without_runtime_changes(session, ontology, blob_store, tmp_path, monkeypatch):
    from src.ops.runtime import bake_stage

    seed(session, ontology, blob_store, 1)
    data, pages, archive = bundle(session)

    def staged_archive(path, recent_limit=30):
        publish_snapshot_bundle(data, pages, path, atlas=archive)
        return data

    monkeypatch.setattr(bake_module, "run_bake", staged_archive)
    outcome = bake_stage(tmp_path / "data.json").operation(None)
    artifacts = {artifact.path: artifact for artifact in outcome.artifacts}
    assert "countries/_atlas.json" in artifacts
    assert artifacts["countries/_atlas.json"].content_type == "application/json; charset=utf-8"
    assert json.loads(artifacts["countries/_atlas.json"].data) == archive
    assert "index.html" in artifacts
    assert "country.html" in artifacts
    assert "high_risk.html" in artifacts
