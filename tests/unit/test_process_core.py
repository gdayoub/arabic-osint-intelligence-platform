"""Tests for src/pipeline/process_core.py — classifiers ported onto the core
schema as facts.

Tests process_one_document() and _unprocessed_document_ids() directly against
the in-memory session fixture, rather than run_core_processing() (which opens
its own session via the module-global engine bound to SETTINGS.database_url —
that's an orchestration wrapper, not unit-testable in isolation, matching how
tests/unit/test_provenance.py tests the sanctioned functions directly rather
than through the CLI).
"""

from __future__ import annotations

from sqlalchemy import select

from src.pipeline.process_core import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    _unprocessed_document_ids,
    process_one_document,
)
from src.processing.escalation_scoring import score_escalation
from src.processing.keyword_classifier import KeywordTopicClassifier
from src.processing.processing_pipeline import ArticleProcessingPipeline
from src.store.orm import FactORM
from src.store.provenance import create_document, register_extractor_version


def _facts_for(session, document_id: int, fact_type: str) -> list[FactORM]:
    stmt = select(FactORM).where(
        FactORM.subject_table == "documents",
        FactORM.subject_id == document_id,
        FactORM.fact_type == fact_type,
    )
    return list(session.scalars(stmt))


def test_process_one_document_writes_topic_and_escalation_facts(session, ontology, blob_store):
    # Contains a HIGH_ESCALATION term (قصف) and a Military topic keyword (جيش).
    text = "شنت طائرات الجيش غارة بالقصف على مواقع في المنطقة الحدودية."
    document = create_document(session, source="test", text=text, content_hash="pc-1", blob_store=blob_store)
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)
    classifier = KeywordTopicClassifier()
    country_guesser = ArticleProcessingPipeline()

    process_one_document(session, document.id, blob_store, ontology, extractor, classifier, country_guesser)

    topic_facts = _facts_for(session, document.id, "topic")
    escalation_facts = _facts_for(session, document.id, "escalation")
    assert len(topic_facts) == 1
    assert topic_facts[0].payload == {"value": "Military"}
    assert len(escalation_facts) == 1
    assert escalation_facts[0].payload["value"] in ("high", "medium")  # depends on exact keyword overlap


def test_country_fact_only_written_when_a_country_is_detected(session, ontology, blob_store):
    with_country = create_document(
        session, source="test", text="الوضع في سوريا يشهد تطورات جديدة.", content_hash="pc-2", blob_store=blob_store
    )
    without_country = create_document(
        session, source="test", text="اجتمع الوزراء لمناقشة الملف الاقتصادي.", content_hash="pc-3", blob_store=blob_store
    )
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)
    classifier = KeywordTopicClassifier()
    country_guesser = ArticleProcessingPipeline()

    process_one_document(session, with_country.id, blob_store, ontology, extractor, classifier, country_guesser)
    process_one_document(session, without_country.id, blob_store, ontology, extractor, classifier, country_guesser)

    assert [f.payload["value"] for f in _facts_for(session, with_country.id, "country")] == ["Syria"]
    assert _facts_for(session, without_country.id, "country") == []
    # Both still get topic/escalation regardless of country detection.
    assert len(_facts_for(session, without_country.id, "topic")) == 1


def test_unprocessed_document_ids_excludes_documents_with_a_topic_fact(session, ontology, blob_store):
    processed = create_document(session, source="test", text="نص عادي بدون كلمات مفتاحية.", content_hash="pc-4", blob_store=blob_store)
    unprocessed = create_document(session, source="test", text="نص آخر.", content_hash="pc-5", blob_store=blob_store)
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)
    classifier = KeywordTopicClassifier()
    country_guesser = ArticleProcessingPipeline()

    process_one_document(session, processed.id, blob_store, ontology, extractor, classifier, country_guesser)

    remaining = _unprocessed_document_ids(session, limit=500)

    assert unprocessed.id in remaining
    assert processed.id not in remaining


def test_processing_a_document_twice_does_not_duplicate_facts(session, ontology, blob_store):
    document = create_document(session, source="test", text="نص للاختبار المتكرر.", content_hash="pc-6", blob_store=blob_store)
    extractor = register_extractor_version(session, EXTRACTOR_NAME, EXTRACTOR_VERSION)
    classifier = KeywordTopicClassifier()
    country_guesser = ArticleProcessingPipeline()

    process_one_document(session, document.id, blob_store, ontology, extractor, classifier, country_guesser)
    # _unprocessed_document_ids would skip this document on a real second
    # run; calling process_one_document directly a second time here is
    # deliberately testing that record_document_fact() itself always
    # appends rather than upserts (P5) -- the orchestration-level skip is
    # a separate, already-tested guarantee.
    process_one_document(session, document.id, blob_store, ontology, extractor, classifier, country_guesser)

    assert len(_facts_for(session, document.id, "topic")) == 2  # two independent facts, not one row updated twice
