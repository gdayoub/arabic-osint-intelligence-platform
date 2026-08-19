"""tests for the extraction pipeline step.

the important ones are the P2 check and the zero mention case. everything
else is bookkeeping.
"""

from __future__ import annotations

from sqlalchemy import select

from src.extract.base import ExtractedMention
from src.pipeline.extract_core import (
    EXTRACTION_MARKER,
    _documents_needing_extraction,
    extract_one_document,
)
from src.store.orm import FactORM, MentionORM
from src.store.provenance import create_document, register_extractor_version


class FakeExtractor:
    """returns whatever I hand it. keeps these tests about the pipeline and
    not about whether the gazetteer happens to know a name."""

    name = "fake_extractor"
    version = "1.0.0"

    def __init__(self, mentions: list[ExtractedMention] | None = None) -> None:
        self._mentions = mentions or []

    def extract(self, text: str) -> list[ExtractedMention]:
        return list(self._mentions)


def _doc(session, blob_store, text, content_hash):
    return create_document(
        session, source="test", text=text, content_hash=content_hash, blob_store=blob_store
    )


def test_mentions_get_written_with_valid_offsets(session, ontology, blob_store):
    text = "قال بشار الأسد إن الوضع في دمشق صعب"
    document = _doc(session, blob_store, text, "ex-1")
    version = register_extractor_version(session, "fake_extractor", "1.0.0")

    start = text.index("دمشق")
    extractor = FakeExtractor(
        [ExtractedMention(text="دمشق", start=start, end=start + 4, object_type="location")]
    )

    written = extract_one_document(session, document, extractor, version, ontology)
    session.flush()

    assert written == 1
    row = session.scalar(select(MentionORM).where(MentionORM.document_id == document.id))
    assert row.text == "دمشق"
    # P2. if this fails the mention points at the wrong characters
    assert text[row.start_offset : row.end_offset] == row.text


def test_bad_offsets_are_rejected_not_stored(session, ontology, blob_store):
    """create_mention re-checks the span. an extractor that computes offsets
    wrong should fail loudly here rather than write a lie into the table."""
    import pytest

    text = "قال بشار الأسد إن الوضع صعب"
    document = _doc(session, blob_store, text, "ex-2")
    version = register_extractor_version(session, "fake_extractor", "1.0.0")
    extractor = FakeExtractor(
        [ExtractedMention(text="دمشق", start=0, end=4, object_type="location")]
    )

    with pytest.raises(ValueError, match="Offset mismatch"):
        extract_one_document(session, document, extractor, version, ontology)


def test_marker_is_written_even_when_nothing_is_found(session, ontology, blob_store):
    """the whole reason the marker exists. a document naming nobody is done
    and must not be rescanned forever."""
    document = _doc(session, blob_store, "أشار المصرف المركزي إلى استقرار العملة", "ex-3")
    version = register_extractor_version(session, "fake_extractor", "1.0.0")

    written = extract_one_document(session, document, FakeExtractor([]), version, ontology)
    session.flush()

    assert written == 0
    marker = session.scalar(
        select(FactORM).where(
            FactORM.subject_id == document.id, FactORM.fact_type == EXTRACTION_MARKER
        )
    )
    assert marker is not None
    assert marker.payload == {"value": 0}


def test_zero_mention_document_is_not_selected_again(session, ontology, blob_store):
    document = _doc(session, blob_store, "نص بلا أسماء", "ex-4")
    version = register_extractor_version(session, "fake_extractor", "1.0.0")

    assert document.id in _documents_needing_extraction(session, "fake_extractor", "1.0.0", 100)

    extract_one_document(session, document, FakeExtractor([]), version, ontology)
    session.flush()

    assert document.id not in _documents_needing_extraction(session, "fake_extractor", "1.0.0", 100)


def test_bumping_the_version_makes_documents_eligible_again(session, ontology, blob_store):
    """same P4 payoff as the classifier version bump. no migration script."""
    document = _doc(session, blob_store, "نص", "ex-5")
    old = register_extractor_version(session, "fake_extractor", "0.9.0")

    extract_one_document(session, document, FakeExtractor([]), old, ontology)
    session.flush()

    assert document.id not in _documents_needing_extraction(session, "fake_extractor", "0.9.0", 100)
    assert document.id in _documents_needing_extraction(session, "fake_extractor", "1.0.0", 100)


def test_retracted_documents_are_skipped(session, ontology, blob_store):
    from src.store.documents import retract_document

    document = _doc(session, blob_store, "نص محذوف", "ex-6")
    session.flush()
    retract_document(session, document.id, "test")
    session.flush()

    assert document.id not in _documents_needing_extraction(session, "fake_extractor", "1.0.0", 100)


def test_the_real_gazetteer_writes_valid_offsets(session, ontology, blob_store):
    """end to end with the actual extractor and real arabic. this is the one
    that would catch the alignment being wrong."""
    from src.extract.gazetteer import GazetteerExtractor

    text = "التقى بشار الأسد وفدا في دمشق لبحث الوضع في إدلب."
    document = _doc(session, blob_store, text, "ex-7")
    extractor = GazetteerExtractor()
    version = register_extractor_version(session, extractor.name, extractor.version)

    written = extract_one_document(session, document, extractor, version, ontology)
    session.flush()

    assert written >= 2
    rows = list(session.scalars(select(MentionORM).where(MentionORM.document_id == document.id)))
    for row in rows:
        assert text[row.start_offset : row.end_offset] == row.text
    assert "دمشق" in {r.text for r in rows}
