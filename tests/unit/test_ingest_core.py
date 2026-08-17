"""Tests for src/pipeline/ingest_core.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from src.pipeline.ingest_core import article_to_document_text, ingest_article
from src.store.documents import get_document_ref_by_url, load_document
from src.store.orm import FactORM
from src.store.provenance import register_extractor_version

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden" / "document_text_composition.json"


def _make_article(**overrides) -> dict:
    article = {
        "source": "test_source",
        "title": "عنوان الخبر",
        "subtitle": "عنوان فرعي",
        "body": "نص الخبر الكامل هنا.",
        "author": "مراسل الوكالة",
        "published_date": None,
        "url": "https://example.com/news/1",
        "tags": ["سياسة"],
        "source_section": "news",
        "collected_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "content_hash": "",
    }
    article.update(overrides)
    return article


def test_document_text_composition_is_frozen():
    """If this fails after an intentional change to article_to_document_text,
    update the golden file too, deliberately — a silent pass here means
    every mention offset stored against previously-ingested documents is
    now wrong (P2).
    """
    fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert article_to_document_text(fixture["input"]) == fixture["expected"]


def test_document_text_composition_handles_missing_title_and_subtitle():
    article = {"title": None, "subtitle": None, "body": "فقط النص الأساسي."}
    assert article_to_document_text(article) == "فقط النص الأساسي."


def test_document_text_composition_always_ends_with_body():
    article = {"title": "عنوان", "subtitle": None, "body": "متن الخبر."}
    text = article_to_document_text(article)
    assert text.endswith("متن الخبر.")


def test_ingest_article_writes_document_and_metadata_facts(session, ontology, blob_store):
    article = _make_article()
    extractor = register_extractor_version(session, "test_scraper", "1.0.0")

    document, was_new = ingest_article(session, article, blob_store, extractor, ontology)
    session.commit()

    assert was_new is True
    reloaded = load_document(session, document.id, blob_store)
    assert reloaded.text == article_to_document_text(article)

    fact_types = {
        f.fact_type
        for f in session.scalars(
            select(FactORM).where(FactORM.subject_table == "documents", FactORM.subject_id == document.id)
        )
    }
    assert fact_types == {"title", "subtitle", "author", "tags", "source_section"}


def test_ingest_article_is_idempotent_on_url(session, ontology, blob_store):
    article = _make_article(url="https://example.com/news/2")
    extractor = register_extractor_version(session, "test_scraper", "1.0.0")

    _, first_was_new = ingest_article(session, article, blob_store, extractor, ontology)
    session.commit()
    _, second_was_new = ingest_article(session, article, blob_store, extractor, ontology)
    session.commit()

    assert first_was_new is True
    assert second_was_new is False

    from src.store.orm import DocumentORM

    documents = list(
        session.scalars(select(DocumentORM).where(DocumentORM.url == "https://example.com/news/2"))
    )
    assert len(documents) == 1


def test_ingest_article_canonicalizes_url(session, ontology, blob_store):
    article = _make_article(url="https://Example.com/news/3/?utm_source=foo#frag")
    extractor = register_extractor_version(session, "test_scraper", "1.0.0")

    document, _ = ingest_article(session, article, blob_store, extractor, ontology)
    session.commit()

    ref = get_document_ref_by_url(session, "https://example.com/news/3")
    assert ref is not None
    assert ref.id == document.id


def test_ingest_article_skips_facts_for_missing_fields(session, ontology, blob_store):
    article = _make_article(url="https://example.com/news/4", subtitle=None, author=None, tags=None)
    extractor = register_extractor_version(session, "test_scraper", "1.0.0")

    document, _ = ingest_article(session, article, blob_store, extractor, ontology)
    session.commit()

    fact_types = {
        f.fact_type
        for f in session.scalars(
            select(FactORM).where(FactORM.subject_table == "documents", FactORM.subject_id == document.id)
        )
    }
    assert fact_types == {"title", "source_section"}
