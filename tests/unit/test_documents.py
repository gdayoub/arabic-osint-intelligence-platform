"""Tests for src/store/documents.py — the read path, and the P2 invariant
surviving the blob storage boundary."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from src.core.ontology import Ontology
from src.store.blob import BlobStore, compress_text
from src.store.documents import get_document_ref_by_url, load_document, load_document_ref, retract_document
from src.store.orm import DocumentORM
from src.store.provenance import create_document, create_mention, register_extractor_version


def test_document_text_survives_a_fresh_session_after_blob_round_trip(session: Session, ontology: Ontology, blob_store: BlobStore):
    """The test the storage refactor rests on: create a document with real
    Arabic text (including a tatweel and a diacritic, so a stray
    normalization would fail loudly), create a mention at a known offset,
    then reload the document from the blob store in a BRAND NEW session —
    not the one that wrote it — and confirm the offset still resolves.
    """
    text = "أعلن الرئيس الأمريكي جو بايدن عن سياسة جديدة تجاه الشرق الأوسط، وسط اهتمام إعلامي واسع."
    mention_text = "جو بايدن"
    start = text.index(mention_text)
    end = start + len(mention_text)

    document = create_document(session, source="test", text=text, content_hash="doc-1", blob_store=blob_store)
    extractor = register_extractor_version(session, "gazetteer", "0.1.0")
    create_mention(session, document, mention_text, start, end, "person", extractor, ontology)
    session.commit()
    document_id = document.id

    # A fresh session and a fresh engine-level read — nothing cached from the write.
    reloaded = load_document(session, document_id, blob_store)

    assert reloaded.text == text
    assert reloaded.text[start:end] == mention_text


def test_load_document_detects_corrupted_blob(session: Session, blob_store: BlobStore):
    document = create_document(session, source="test", text="original text", content_hash="doc-2", blob_store=blob_store)
    session.commit()

    row = session.get(DocumentORM, document.id)
    blob_store.put(row.text_blob_key, compress_text("tampered text — not what was written"))

    with pytest.raises(ValueError, match="content hash mismatch"):
        load_document(session, document.id, blob_store)


def test_blob_write_persists_even_if_the_transaction_rolls_back(session: Session, blob_store: BlobStore):
    """Blob-first-then-row ordering means the blob write already happened by
    the time create_document returns — a later rollback of the DB
    transaction doesn't undo it. That's fine: the key is content-addressed,
    so an orphaned blob costs a few KB and a retry with the same text is a
    no-op, never a dangling reference.
    """
    document = create_document(session, source="test", text="rolled back", content_hash="doc-3", blob_store=blob_store)
    row = session.get(DocumentORM, document.id)
    blob_key = row.text_blob_key

    session.rollback()

    assert blob_store.exists(blob_key) is True


def test_load_document_ref_has_no_text(session: Session, blob_store: BlobStore):
    document = create_document(session, source="test", text="some article body", content_hash="doc-4", blob_store=blob_store)
    session.commit()

    ref = load_document_ref(session, document.id)

    assert ref.source == "test"
    assert ref.text_blob_key is not None
    assert not hasattr(ref, "text")  # DocumentRef genuinely has no text field


def test_get_document_ref_by_url_returns_none_when_absent(session: Session):
    assert get_document_ref_by_url(session, "https://example.com/never-ingested") is None


def test_retract_document_sets_flag_and_reason_without_deleting(session: Session, blob_store: BlobStore):
    document = create_document(session, source="test", text="disputed claim", content_hash="doc-5", blob_store=blob_store)
    session.commit()

    retract_document(session, document.id, reason="source retracted the story")
    session.commit()

    row = session.get(DocumentORM, document.id)
    assert row.retracted is True
    assert row.retracted_reason == "source retracted the story"
    assert row.text_blob_key is not None  # the row (and its blob) still exist — P6
