"""tests for the extraction pipeline step.

the important ones are the P2 check and the zero mention case. everything
else is bookkeeping.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import event, select

import src.pipeline.extract_core as extract_core
from src.extract.base import ExtractedMention
from src.pipeline.extract_core import (
    EXTRACTION_MARKER,
    _documents_needing_extraction,
    extract_one_document,
)
from src.store.orm import FactORM, MentionORM, ProvenanceORM
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


def _location(text: str, name: str) -> ExtractedMention:
    start = text.index(name)
    return ExtractedMention(
        text=name,
        start=start,
        end=start + len(name),
        object_type="location",
    )


def _extract_locations(session, document, text, names, version, ontology) -> int:
    mentions = [_location(text, name) for name in names]
    return extract_one_document(
        session,
        document,
        FakeExtractor(mentions),
        version,
        ontology,
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


def test_failed_version_replacement_keeps_only_prior_generation_live(
    session, ontology, blob_store
):
    text = "وصل وفد من دمشق إلى بيروت"
    document = _doc(session, blob_store, text, "ex-atomic-failure")
    old_version = register_extractor_version(session, "fake_extractor", "0.9.0")
    new_version = register_extractor_version(session, "fake_extractor", "1.0.0")

    _extract_locations(session, document, text, ["دمشق"], old_version, ontology)
    session.flush()

    broken_replacement = FakeExtractor(
        [
            _location(text, "بيروت"),
            ExtractedMention(
                text="غير موجود",
                start=0,
                end=9,
                object_type="location",
            ),
        ]
    )

    with pytest.raises(ValueError, match="Offset mismatch"):
        extract_one_document(
            session,
            document,
            broken_replacement,
            new_version,
            ontology,
        )

    mentions = session.scalars(
        select(MentionORM).where(MentionORM.document_id == document.id)
    ).all()
    markers = session.scalars(
        select(FactORM).where(
            FactORM.subject_table == "documents",
            FactORM.subject_id == document.id,
            FactORM.fact_type == EXTRACTION_MARKER,
        )
    ).all()
    provenance = session.scalars(
        select(ProvenanceORM).where(ProvenanceORM.document_id == document.id)
    ).all()

    assert len(mentions) == 1
    assert mentions[0].text == "دمشق"
    assert mentions[0].extractor_version_id == old_version.id
    assert mentions[0].retracted is False
    assert len(markers) == 1
    assert markers[0].extractor_version_id == old_version.id
    assert markers[0].retracted is False
    assert len(provenance) == 2  # old mention + old marker; no replacement residue
    assert document.id in _documents_needing_extraction(
        session, "fake_extractor", "1.0.0", 100
    )


def test_successful_version_replacement_retracts_prior_mentions_and_marker(
    session, ontology, blob_store
):
    text = "وصل وفد من دمشق إلى بيروت"
    document = _doc(session, blob_store, text, "ex-atomic-success")
    old_version = register_extractor_version(session, "fake_extractor", "0.9.0")
    new_version = register_extractor_version(session, "fake_extractor", "1.0.0")

    _extract_locations(session, document, text, ["دمشق"], old_version, ontology)
    session.flush()

    _extract_locations(session, document, text, ["بيروت"], new_version, ontology)
    session.flush()

    mentions = session.scalars(
        select(MentionORM)
        .where(MentionORM.document_id == document.id)
        .order_by(MentionORM.id)
    ).all()
    markers = session.scalars(
        select(FactORM)
        .where(
            FactORM.subject_table == "documents",
            FactORM.subject_id == document.id,
            FactORM.fact_type == EXTRACTION_MARKER,
        )
        .order_by(FactORM.id)
    ).all()

    assert len(mentions) == 2
    assert [row.text for row in mentions] == ["دمشق", "بيروت"]
    assert [row.retracted for row in mentions] == [True, False]
    assert [row.extractor_version_id for row in mentions] == [old_version.id, new_version.id]
    assert len(markers) == 2
    assert [row.retracted for row in markers] == [True, False]
    assert markers[1].supersedes_id == markers[0].id
    live_mentions = [row for row in mentions if not row.retracted]
    assert len(live_mentions) == 1
    assert live_mentions[0].extractor_version_id == new_version.id


def test_failure_during_live_generation_switch_rolls_back_both_sides(
    session, ontology, blob_store
):
    text = "دمشق وبيروت"
    document = _doc(session, blob_store, text, "ex-switch-failure")
    old_version = register_extractor_version(session, "fake_extractor", "0.9.0")
    new_version = register_extractor_version(session, "fake_extractor", "1.0.0")

    _extract_locations(session, document, text, ["دمشق"], old_version, ontology)
    session.flush()

    def reject_old_mention_retraction(db_session, _flush_context, _instances):
        retiring_old_mention = any(
            isinstance(row, MentionORM)
            and row.document_id == document.id
            and row.retracted
            for row in db_session.dirty
        )
        if retiring_old_mention:
            raise RuntimeError("forced switch failure")

    event.listen(session, "before_flush", reject_old_mention_retraction)
    try:
        with pytest.raises(RuntimeError, match="forced switch failure"):
            _extract_locations(
                session, document, text, ["بيروت"], new_version, ontology
            )
    finally:
        event.remove(session, "before_flush", reject_old_mention_retraction)

    session.expire_all()
    mentions = session.scalars(
        select(MentionORM).where(MentionORM.document_id == document.id)
    ).all()
    markers = session.scalars(
        select(FactORM).where(
            FactORM.subject_id == document.id,
            FactORM.fact_type == EXTRACTION_MARKER,
        )
    ).all()
    assert len(mentions) == 1
    assert mentions[0].text == "دمشق"
    assert mentions[0].retracted is False
    assert len(markers) == 1
    assert markers[0].extractor_version_id == old_version.id
    assert markers[0].retracted is False


def test_replacement_does_not_retract_a_different_extractor_family(
    session, ontology, blob_store
):
    text = "دمشق وبيروت"
    document = _doc(session, blob_store, text, "ex-separate-extractors")
    gazetteer_version = register_extractor_version(session, "gazetteer", "1.0.0")
    model_version = register_extractor_version(session, "model", "1.0.0")

    _extract_locations(
        session, document, text, ["دمشق"], gazetteer_version, ontology
    )
    _extract_locations(session, document, text, ["بيروت"], model_version, ontology)
    session.flush()

    live_mentions = session.scalars(
        select(MentionORM).where(
            MentionORM.document_id == document.id,
            MentionORM.retracted.is_(False),
        )
    ).all()
    assert {row.text for row in live_mentions} == {"دمشق", "بيروت"}


def test_extraction_stats_only_count_documents_whose_savepoint_succeeds(
    session, ontology, blob_store, monkeypatch
):
    good = _doc(session, blob_store, "دمشق نص سليم", "ex-stats-good")
    broken = _doc(session, blob_store, "دمشق نص مكسور", "ex-stats-bad")

    class SelectiveExtractor:
        name = "selective_extractor"
        version = "1.0.0"

        def extract(self, text: str) -> list[ExtractedMention]:
            start = text.index("دمشق")
            mentions = [
                ExtractedMention(
                    text="دمشق",
                    start=start,
                    end=start + len("دمشق"),
                    object_type="location",
                )
            ]
            if "مكسور" in text:
                mentions.append(
                    ExtractedMention(
                        text="بيروت",
                        start=0,
                        end=5,
                        object_type="location",
                    )
                )
            return mentions

    @contextmanager
    def borrowed_session():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(extract_core, "get_core_session", borrowed_session)

    stats = extract_core.run_core_extraction(
        limit=10,
        blob_store=blob_store,
        extractor=SelectiveExtractor(),
    )

    assert stats.documents_scanned == 2
    assert stats.documents_processed == 1
    assert stats.mentions_written == 1
    assert stats.errors == 1
    assert session.scalar(
        select(MentionORM).where(MentionORM.document_id == good.id)
    ) is not None
    assert session.scalar(
        select(MentionORM).where(MentionORM.document_id == broken.id)
    ) is None
    assert session.scalar(
        select(FactORM).where(
            FactORM.subject_id == broken.id,
            FactORM.fact_type == EXTRACTION_MARKER,
        )
    ) is None
