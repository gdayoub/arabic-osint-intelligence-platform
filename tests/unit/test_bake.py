"""Tests for scripts/bake_dashboard_data.py."""

from __future__ import annotations

import json

from scripts.bake_dashboard_data import bake, write_data_json
from src.store.provenance import create_document, record_document_fact, register_extractor_version


def _seed_document(session, ontology, blob_store, extractor, *, url, body, title, topic, escalation, country=None):
    document = create_document(session, source="AlJazeeraArabic", text=body, content_hash=url, blob_store=blob_store, url=url)
    record_document_fact(session, document, "title", title, extractor, ontology)
    record_document_fact(session, document, "topic", topic, extractor, ontology)
    record_document_fact(session, document, "escalation", escalation, extractor, ontology)
    if country:
        record_document_fact(session, document, "country", country, extractor, ontology)
    return document


def test_bake_output_matches_dashboard_expected_shape(session, ontology, blob_store):
    extractor = register_extractor_version(session, "test", "1.0.0")
    _seed_document(
        session, ontology, blob_store, extractor,
        url="https://example.com/1", body="نص المقال الأول الكامل هنا مع تفاصيل إضافية.",
        title="عنوان الخبر الأول", topic="Politics", escalation="low", country="Syria",
    )
    session.commit()

    data = bake(session)

    assert set(data.keys()) == {"generated_at", "schema_version", "stats", "topics", "escalation", "recent", "daily"}
    assert data["stats"]["total_raw"] == 1
    assert data["stats"]["total_processed"] == 1
    assert data["stats"]["sources"] == {"AlJazeeraArabic": 1}
    assert data["topics"]["topics"] == [{"topic": "Politics", "count": 1}]
    assert data["escalation"]["escalation"] == {"low": 1}
    assert len(data["recent"]) == 1
    assert data["recent"][0]["title"] == "عنوان الخبر الأول"
    assert data["recent"][0]["country"] == "Syria"
    assert data["recent"][0]["url"] == "https://example.com/1"


def test_bake_never_includes_document_body_text(session, ontology, blob_store):
    """The output is written to a public Pages deployment -- this is the
    regression guard for that boundary, even though bake() structurally
    never touches the blob store at all.
    """
    extractor = register_extractor_version(session, "test", "1.0.0")
    secret_body_fragment = "تفاصيل سرية للغاية لا يجب أن تظهر علنا"
    _seed_document(
        session, ontology, blob_store, extractor,
        url="https://example.com/2", body=f"مقدمة عادية. {secret_body_fragment} خاتمة عادية.",
        title="عنوان عام", topic="Economy", escalation="medium",
    )
    session.commit()

    data = bake(session)
    serialized = json.dumps(data, ensure_ascii=False)

    assert secret_body_fragment not in serialized


def test_bake_serializes_arabic_without_unicode_escapes(session, ontology, blob_store, tmp_path):
    extractor = register_extractor_version(session, "test", "1.0.0")
    _seed_document(
        session, ontology, blob_store, extractor,
        url="https://example.com/3", body="نص", title="عنوان بالعربية", topic="Military", escalation="high",
    )
    session.commit()

    data = bake(session)
    out_path = tmp_path / "data.json"
    write_data_json(data, out_path)

    written = out_path.read_text(encoding="utf-8")
    assert "\\u" not in written
    assert "عنوان بالعربية" in written


def test_bake_respects_recent_limit(session, ontology, blob_store):
    extractor = register_extractor_version(session, "test", "1.0.0")
    for i in range(5):
        _seed_document(
            session, ontology, blob_store, extractor,
            url=f"https://example.com/limit-{i}", body=f"نص رقم {i}", title=f"عنوان {i}", topic="Politics", escalation="low",
        )
    session.commit()

    data = bake(session, recent_limit=2)

    assert len(data["recent"]) == 2
    assert data["stats"]["total_raw"] == 5  # aggregates still cover everything, not just the recent slice


def test_bake_total_processed_excludes_documents_without_topic_fact(session, ontology, blob_store):
    extractor = register_extractor_version(session, "test", "1.0.0")
    document = create_document(session, source="test", text="بدون تصنيف", content_hash="unprocessed", blob_store=blob_store)
    record_document_fact(session, document, "title", "لم يعالج بعد", extractor, ontology)
    session.commit()

    data = bake(session)

    assert data["stats"]["total_raw"] == 1
    assert data["stats"]["total_processed"] == 0
    assert data["recent"][0]["topic"] is None
