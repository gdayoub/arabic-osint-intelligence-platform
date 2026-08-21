"""M4.2a durable evidence identity and read-only constraint remapping."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from main import build_parser
from src.extract.base import ExtractedMention
from src.pipeline.extract_core import extract_one_document
from src.resolve.review import record_decision
from src.store.identity import (
    IdentityInvariantError,
    active_resolution_constraints,
    evidence_fingerprint,
    remap_resolution_constraints,
)
from src.store.orm import (
    DocumentIdentityORM,
    EvidenceIdentityORM,
    MentionEvidenceIdentityORM,
    MentionORM,
    ProvenanceORM,
    ResolutionConstraintORM,
    ResolutionDecisionORM,
)
from src.store.provenance import (
    create_document,
    create_mention,
    get_provenance_chain,
    register_extractor_version,
)


class _SpanExtractor:
    name = "identity_test_extractor"
    language = "ar"

    def __init__(self, version: str, text: str, object_type: str = "person") -> None:
        self.version = version
        self._text = text
        self._object_type = object_type

    def extract(self, source_text: str) -> list[ExtractedMention]:
        start = source_text.index(self._text)
        return [
            ExtractedMention(
                text=self._text,
                start=start,
                end=start + len(self._text),
                object_type=self._object_type,
            )
        ]


def _document(session, blob_store, suffix: str):
    text = "ذكر محمد أحمد في الخبر"
    return create_document(
        session,
        source="identity-test",
        text=text,
        content_hash=f"identity-{suffix}",
        blob_store=blob_store,
        url=f"https://example.test/identity/{suffix}",
    )


def _mention_row(session, document_id: int) -> MentionORM:
    return session.scalar(
        select(MentionORM)
        .where(MentionORM.document_id == document_id)
        .order_by(MentionORM.id.desc())
        .limit(1)
    )


def test_document_and_mention_writes_create_distinct_durable_evidence_for_syndicated_text(
    session, ontology, blob_store
):
    first = _document(session, blob_store, "one")
    second = _document(session, blob_store, "two")
    extractor = register_extractor_version(session, "identity_manual", "1.0.0")
    mention_text = "محمد أحمد"
    start = first.text.index(mention_text)
    first_mention = create_mention(
        session,
        first,
        mention_text,
        start,
        start + len(mention_text),
        "person",
        extractor,
        ontology,
        language="ar",
    )
    second_mention = create_mention(
        session,
        second,
        mention_text,
        start,
        start + len(mention_text),
        "person",
        extractor,
        ontology,
        language="ar",
    )

    document_identities = list(
        session.scalars(select(DocumentIdentityORM).order_by(DocumentIdentityORM.document_id))
    )
    evidence = list(session.scalars(select(EvidenceIdentityORM).order_by(EvidenceIdentityORM.id)))
    mappings = list(session.scalars(select(MentionEvidenceIdentityORM)))

    assert len(document_identities) == 2
    assert len({row.document_uid for row in document_identities}) == 2
    assert len(evidence) == 2
    assert evidence[0].fingerprint != evidence[1].fingerprint
    assert {row.mention_id for row in mappings} == {first_mention.id, second_mention.id}
    first_identity = next(row for row in document_identities if row.document_id == first.id)
    first_evidence = next(
        row for row in evidence if row.document_identity_id == first_identity.id
    )
    first_mapping = session.get(MentionEvidenceIdentityORM, first_mention.id)
    assert first_mapping is not None
    mapped_evidence = session.get(EvidenceIdentityORM, first_mapping.evidence_identity_id)
    assert mapped_evidence is not None
    assert first_evidence.fingerprint == evidence_fingerprint(
        document_uid=first_identity.document_uid,
        source_text_sha256=mapped_evidence.source_text_sha256,
        start_offset=start,
        end_offset=start + len(mention_text),
        object_type="person",
        language="ar",
    )
    # Identity metadata stores hashes/offsets, never a duplicate raw snippet.
    assert "text" not in EvidenceIdentityORM.__table__.columns

    chain = get_provenance_chain(session, "evidence_identities", first_evidence.id, blob_store)
    assert len(chain) == 1
    assert chain[0].mention_text == first_mention.text
    assert chain[0].document_text[start : start + len(mention_text)] == mention_text
    evidence_provenance = session.scalars(
        select(ProvenanceORM).where(
            ProvenanceORM.target_table == "evidence_identities",
            ProvenanceORM.target_id == first_evidence.id,
        )
    ).all()
    assert [row.mention_id for row in evidence_provenance] == [first_mention.id]


def test_new_mentions_require_an_explicit_language(session, ontology, blob_store):
    document = _document(session, blob_store, "language-required")
    extractor = register_extractor_version(session, "identity_manual", "1.0.0")
    start = document.text.index("محمد أحمد")

    with pytest.raises(TypeError, match="language"):
        create_mention(
            session,
            document,
            "محمد أحمد",
            start,
            start + len("محمد أحمد"),
            "person",
            extractor,
            ontology,
        )

    with pytest.raises(ValueError, match="reserved for explicit legacy adoption"):
        create_mention(
            session,
            document,
            "محمد أحمد",
            start,
            start + len("محمد أحمد"),
            "person",
            extractor,
            ontology,
            language="und",
        )


def test_exact_replacement_remaps_and_shifted_span_becomes_unresolved(
    session, ontology, blob_store
):
    first = _document(session, blob_store, "remap-one")
    second = _document(session, blob_store, "remap-two")
    old_version = register_extractor_version(session, _SpanExtractor.name, "0.9.0")
    new_version = register_extractor_version(session, _SpanExtractor.name, "1.0.0")

    old_extractor = _SpanExtractor("0.9.0", "محمد أحمد")
    extract_one_document(session, first, old_extractor, old_version, ontology)
    extract_one_document(session, second, old_extractor, old_version, ontology)
    old_first = _mention_row(session, first.id)
    old_second = _mention_row(session, second.id)
    decision = record_decision(
        session,
        old_first.id,
        old_second.id,
        "same",
        "identity-tester",
        "unit-test",
    )

    new_extractor = _SpanExtractor("1.0.0", "محمد أحمد")
    extract_one_document(session, first, new_extractor, new_version, ontology)
    extract_one_document(session, second, new_extractor, new_version, ontology)
    current_first = _mention_row(session, first.id)
    current_second = _mention_row(session, second.id)
    remaps = remap_resolution_constraints(session)

    assert len(remaps) == 1
    assert remaps[0].source_decision_id == decision.id
    assert remaps[0].status == "remapped"
    assert set(remaps[0].left_mention_ids + remaps[0].right_mention_ids) == {
        current_first.id,
        current_second.id,
    }
    assert old_first.retracted is True
    assert old_second.retracted is True

    shifted_extractor = _SpanExtractor("1.0.0", "محمد")
    extract_one_document(session, first, shifted_extractor, new_version, ontology)
    shifted_remap = remap_resolution_constraints(session)[0]

    assert shifted_remap.status == "unresolved"
    assert shifted_remap.reason.startswith("missing_live_")


def test_opposing_active_durable_constraints_are_visible_as_conflict(
    session, ontology, blob_store
):
    first = _document(session, blob_store, "conflict-one")
    second = _document(session, blob_store, "conflict-two")
    extractor = register_extractor_version(session, "identity_manual", "1.0.0")
    start = first.text.index("محمد أحمد")

    end = start + len("محمد أحمد")
    old_left = create_mention(
        session, first, "محمد أحمد", start, end, "person", extractor, ontology, language="ar"
    )
    old_right = create_mention(
        session, second, "محمد أحمد", start, end, "person", extractor, ontology, language="ar"
    )
    first_constraint_decision = record_decision(
        session, old_left.id, old_right.id, "same", "identity-tester", "unit-test"
    )
    new_left = create_mention(
        session, first, "محمد أحمد", start, end, "person", extractor, ontology, language="ar"
    )
    new_right = create_mention(
        session, second, "محمد أحمد", start, end, "person", extractor, ontology, language="ar"
    )
    second_constraint_decision = record_decision(
        session, new_left.id, new_right.id, "different", "identity-tester", "unit-test"
    )

    remaps = remap_resolution_constraints(session)

    assert {row.status for row in remaps} == {"conflict"}
    assert {row.source_decision_id for row in remaps} == {
        first_constraint_decision.id,
        second_constraint_decision.id,
    }
    assert all(len(row.conflicting_constraint_ids) == 2 for row in remaps)


def test_constraint_requires_a_durable_predecessor_and_is_provenance_inspectable(
    session, ontology, blob_store
):
    first = _document(session, blob_store, "predecessor-one")
    second = _document(session, blob_store, "predecessor-two")
    extractor = register_extractor_version(session, "identity_manual", "1.0.0")
    start = first.text.index("محمد أحمد")
    end = start + len("محمد أحمد")
    left = create_mention(
        session, first, "محمد أحمد", start, end, "person", extractor, ontology, language="ar"
    )
    right = create_mention(
        session, second, "محمد أحمد", start, end, "person", extractor, ontology, language="ar"
    )
    first_decision = record_decision(
        session, left.id, right.id, "same", "identity-tester", "unit-test"
    )
    first_constraint = session.scalar(
        select(ResolutionConstraintORM).where(
            ResolutionConstraintORM.source_decision_id == first_decision.id
        )
    )
    chain = get_provenance_chain(
        session, "resolution_constraints", first_constraint.id, blob_store
    )
    assert {entry.mention_id for entry in chain} == {left.id, right.id}
    assert len(chain) == 2
    assert build_parser().parse_args(
        ["provenance", "show", "resolution_constraints", str(first_constraint.id)]
    ).table == "resolution_constraints"
    assert build_parser().parse_args(
        ["provenance", "show", "evidence_identities", "1"]
    ).table == "evidence_identities"

    session.delete(first_constraint)
    session.flush()
    second_decision = ResolutionDecisionORM(
        left_mention_id=left.id,
        right_mention_id=right.id,
        decision="different",
        source="unit-test",
        reviewer="identity-tester",
        extractor_version_id=first_decision.extractor_version_id,
        supersedes_id=first_decision.id,
    )
    session.add(second_decision)
    session.flush()

    from src.store.identity import record_resolution_constraint

    with pytest.raises(IdentityInvariantError, match="durable constraint is missing"):
        record_resolution_constraint(session, second_decision)

    assert active_resolution_constraints(session) == []
    assert session.scalar(select(func.count()).select_from(ResolutionConstraintORM)) == 0
