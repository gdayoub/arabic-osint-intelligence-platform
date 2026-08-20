"""Tests for src/pipeline/process_core.py — classifiers ported onto the core
schema as facts.

Most tests exercise process_one_document() and selection directly against the
in-memory session fixture. One orchestration test lends that session to
run_core_processing() so its success/error counters can be checked without a
network database.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import select

import src.pipeline.process_core as process_core
from src.pipeline.process_core import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    _documents_needing_processing,
    _latest_facts_by_type,
    process_one_document,
)
from src.processing.country_detection import CountryDetection
from src.processing.keyword_classifier import KeywordTopicClassifier
from src.store.orm import FactORM
from src.store.provenance import create_document, register_extractor_version


def _facts_for(session, document_id: int, fact_type: str) -> list[FactORM]:
    stmt = select(FactORM).where(
        FactORM.subject_table == "documents",
        FactORM.subject_id == document_id,
        FactORM.fact_type == fact_type,
    )
    return list(session.scalars(stmt))


def _process(session, document_id, blob_store, ontology, extractor):
    process_one_document(session, document_id, blob_store, ontology, extractor, KeywordTopicClassifier())


def test_process_one_document_writes_topic_and_escalation_facts(session, ontology, blob_store):
    # Contains a HIGH_ESCALATION term (قصف) and a Military topic keyword (جيش).
    text = "شنت طائرات الجيش غارة بالقصف على مواقع في المنطقة الحدودية."
    document = create_document(session, source="test", text=text, content_hash="pc-1", blob_store=blob_store)
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)

    _process(session, document.id, blob_store, ontology, extractor)

    topic_facts = _facts_for(session, document.id, "topic")
    escalation_facts = _facts_for(session, document.id, "escalation")
    assert len(topic_facts) == 1
    assert topic_facts[0].payload == {"value": "Military"}
    assert len(escalation_facts) == 1
    assert escalation_facts[0].payload["value"] in ("high", "medium")


def test_country_fact_is_explicit_even_when_no_country_is_detected(
    session, ontology, blob_store
):
    with_country = create_document(
        session, source="test", text="الوضع في سوريا يشهد تطورات جديدة.", content_hash="pc-2", blob_store=blob_store
    )
    without_country = create_document(
        session, source="test", text="اجتمع الوزراء لمناقشة الملف الاقتصادي.", content_hash="pc-3", blob_store=blob_store
    )
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)

    _process(session, with_country.id, blob_store, ontology, extractor)
    _process(session, without_country.id, blob_store, ontology, extractor)

    assert [f.payload["value"] for f in _facts_for(session, with_country.id, "country")] == ["Syria"]
    assert [f.payload["value"] for f in _facts_for(session, without_country.id, "country")] == [None]
    assert len(_facts_for(session, without_country.id, "topic")) == 1


def test_sports_articles_are_classified_as_sports(session, ontology, blob_store):
    """Football coverage used to land in Politics (via رئيس/president in club
    headlines) or Uncategorized, both visible on the live dashboard."""
    text = "سجل اللاعب هدفين في مباراة الدوري وقال المدرب إن تشكيلة الفريق جاهزة للبطولة."
    document = create_document(session, source="test", text=text, content_hash="pc-sport", blob_store=blob_store)
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)

    _process(session, document.id, blob_store, ontology, extractor)

    assert _facts_for(session, document.id, "topic")[0].payload == {"value": "Sports"}


def test_documents_needing_processing_excludes_current_version_documents(session, ontology, blob_store):
    processed = create_document(session, source="test", text="نص عادي.", content_hash="pc-4", blob_store=blob_store)
    unprocessed = create_document(session, source="test", text="نص آخر.", content_hash="pc-5", blob_store=blob_store)
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)

    _process(session, processed.id, blob_store, ontology, extractor)

    remaining = _documents_needing_processing(session, limit=500)

    assert unprocessed.id in remaining
    assert processed.id not in remaining


def test_documents_processed_by_an_older_version_are_selected_again(session, ontology, blob_store):
    """The P4 payoff: bumping EXTRACTOR_VERSION should make already-processed
    documents eligible again, with no migration script."""
    document = create_document(session, source="test", text="نص قديم.", content_hash="pc-6", blob_store=blob_store)
    old_extractor = register_extractor_version(session, EXTRACTOR_NAME, "0.9.0")

    _process(session, document.id, blob_store, ontology, old_extractor)
    session.flush()

    assert document.id in _documents_needing_processing(session, limit=500)


def test_reprocessing_supersedes_prior_facts_instead_of_mutating(session, ontology, blob_store):
    document = create_document(session, source="test", text="الوضع في سوريا.", content_hash="pc-7", blob_store=blob_store)
    old_extractor = register_extractor_version(session, EXTRACTOR_NAME, "0.9.0")
    new_extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)

    _process(session, document.id, blob_store, ontology, old_extractor)
    session.flush()
    _process(session, document.id, blob_store, ontology, new_extractor)
    session.flush()

    topic_facts = sorted(_facts_for(session, document.id, "topic"), key=lambda f: f.id)
    assert len(topic_facts) == 2, "old fact should still exist — append-only (P5)"
    assert topic_facts[1].supersedes_id == topic_facts[0].id
    assert topic_facts[0].supersedes_id is None
    assert topic_facts[0].retracted is True
    assert topic_facts[1].retracted is False


def test_latest_facts_by_type_returns_the_newest_per_type(session, ontology, blob_store):
    document = create_document(session, source="test", text="الوضع في سوريا.", content_hash="pc-8", blob_store=blob_store)
    old_extractor = register_extractor_version(session, EXTRACTOR_NAME, "0.9.0")
    new_extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)

    _process(session, document.id, blob_store, ontology, old_extractor)
    session.flush()
    _process(session, document.id, blob_store, ontology, new_extractor)
    session.flush()

    latest = _latest_facts_by_type(session, document.id)
    all_topic_ids = {f.id for f in _facts_for(session, document.id, "topic")}

    assert latest["topic"].id == max(all_topic_ids)
    assert set(latest) == {"topic", "escalation", "country"}


def test_failed_replacement_rolls_back_partial_facts_and_keeps_prior_live(
    session, ontology, blob_store
):
    """Country and escalation are written before topic. A topic failure must
    roll those writes back and must also undo any attempted generation switch.
    """

    class BrokenClassifier:
        def classify(self, text: str):
            raise RuntimeError("forced topic failure")

    document = create_document(
        session,
        source="test",
        text="الوضع في سوريا يشهد قصفا.",
        content_hash="pc-atomic-failure",
        blob_store=blob_store,
    )
    old_extractor = register_extractor_version(session, EXTRACTOR_NAME, "0.9.0")
    new_extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)
    _process(session, document.id, blob_store, ontology, old_extractor)
    session.flush()

    old_rows = session.scalars(
        select(FactORM).where(
            FactORM.subject_table == "documents",
            FactORM.subject_id == document.id,
        )
    ).all()
    old_ids = {row.id for row in old_rows}

    with pytest.raises(RuntimeError, match="forced topic failure"):
        process_one_document(
            session,
            document.id,
            blob_store,
            ontology,
            new_extractor,
            BrokenClassifier(),  # type: ignore[arg-type]
        )

    rows_after_failure = session.scalars(
        select(FactORM).where(
            FactORM.subject_table == "documents",
            FactORM.subject_id == document.id,
        )
    ).all()
    assert {row.id for row in rows_after_failure} == old_ids
    assert all(row.extractor_version_id == old_extractor.id for row in rows_after_failure)
    assert all(row.retracted is False for row in rows_after_failure)
    assert document.id in _documents_needing_processing(session, limit=500)


def test_successful_replacement_can_clear_a_prior_country(
    session, ontology, blob_store, monkeypatch
):
    document = create_document(
        session,
        source="test",
        text="الوضع في سوريا.",
        content_hash="pc-clear-country",
        blob_store=blob_store,
    )
    old_extractor = register_extractor_version(session, EXTRACTOR_NAME, "0.9.0")
    new_extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)
    _process(session, document.id, blob_store, ontology, old_extractor)
    session.flush()

    monkeypatch.setattr(
        process_core,
        "detect_country",
        lambda _text: CountryDetection(country=None, counts={}),
    )
    _process(session, document.id, blob_store, ontology, new_extractor)
    session.flush()

    country_facts = sorted(_facts_for(session, document.id, "country"), key=lambda row: row.id)
    assert [row.payload["value"] for row in country_facts] == ["Syria", None]
    assert [row.retracted for row in country_facts] == [True, False]
    assert country_facts[1].supersedes_id == country_facts[0].id
    assert _latest_facts_by_type(session, document.id)["country"].payload == {"value": None}


def test_replacement_keeps_facts_from_another_classifier_family(
    session, ontology, blob_store
):
    document = create_document(
        session,
        source="test",
        text="الوضع في سوريا.",
        content_hash="pc-separate-classifier",
        blob_store=blob_store,
    )
    other_classifier = register_extractor_version(
        session, "analyst_override", "1.0.0"
    )
    current_classifier = register_extractor_version(
        session, EXTRACTOR_NAME, EXTRACTOR_VERSION
    )
    analyst_fact = process_core.record_document_fact(
        session,
        document,
        "topic",
        "Analyst category",
        other_classifier,
        ontology,
    )

    _process(
        session,
        document.id,
        blob_store,
        ontology,
        current_classifier,
    )
    session.flush()

    stored_analyst_fact = session.get(FactORM, analyst_fact.id)
    assert stored_analyst_fact is not None
    assert stored_analyst_fact.retracted is False


def test_processing_stats_only_count_documents_whose_savepoint_succeeds(
    session, ontology, blob_store, monkeypatch
):
    create_document(
        session,
        source="test",
        text="نص اقتصادي سليم.",
        content_hash="pc-stats-good",
        blob_store=blob_store,
    )
    broken = create_document(
        session,
        source="test",
        text="هذا المستند يتعطل بعد كتابة حقائق جزئية.",
        content_hash="pc-stats-bad",
        blob_store=blob_store,
    )
    baseline = KeywordTopicClassifier()

    class SelectiveClassifier:
        def classify(self, text: str):
            if "يتعطل" in text:
                raise RuntimeError("forced document failure")
            return baseline.classify(text)

    @contextmanager
    def borrowed_session():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(process_core, "get_core_session", borrowed_session)
    monkeypatch.setattr(process_core, "KeywordTopicClassifier", SelectiveClassifier)

    stats = process_core.run_core_processing(limit=10, blob_store=blob_store)

    assert stats.scanned == 2
    assert stats.processed == 1
    assert stats.errors == 1
    assert _facts_for(session, broken.id, "country") == []
    assert _facts_for(session, broken.id, "escalation") == []
    assert _facts_for(session, broken.id, "topic") == []
