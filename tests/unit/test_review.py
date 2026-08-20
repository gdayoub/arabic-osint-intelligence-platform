"""Human review queue, decisions, and manual resolution constraints."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from src.pipeline.resolve_core import resolve_all
from src.resolve.review import (
    decide_review_pair,
    export_review_labels,
    list_review_items,
    record_manual_merge,
    record_manual_split,
)
from src.store.orm import (
    EntityMentionORM,
    EntityORM,
    ProvenanceORM,
    ResolutionDecisionORM,
    ReviewPairORM,
)
from src.store.provenance import create_document, create_mention, register_extractor_version


class _Weights:
    threshold = 0.6


class FixedScorer:
    """Small deterministic scorer so these tests exercise workflow, not weights."""

    weights = _Weights()

    def __init__(self, probability: float) -> None:
        self._probability = probability

    def probability(self, _features) -> float:
        return self._probability


def _make_mentions(session, blob_store, ontology, spellings):
    extractor = register_extractor_version(session, "test_extractor", "1.0.0")
    mentions = []
    for index, spelling in enumerate(spellings):
        text = f"ذكر {spelling} في الخبر"
        document = create_document(
            session,
            source="test",
            text=text,
            content_hash=f"review-{index}-{spelling}",
            blob_store=blob_store,
            url=f"https://example.com/review-{index}",
        )
        start = text.index(spelling)
        mentions.append(
            create_mention(
                session,
                document,
                spelling,
                start,
                start + len(spelling),
                "person",
                extractor,
                ontology,
            )
        )
    session.flush()
    return mentions


def test_near_threshold_pair_is_queued_with_two_provenance_rows(
    session, ontology, blob_store
):
    _make_mentions(session, blob_store, ontology, ["جورج دايوب", "جورج داوب"])

    stats = resolve_all(session, ontology, scorer=FixedScorer(0.55), review_margin=0.10)
    session.flush()

    pair = session.scalar(select(ReviewPairORM))
    assert stats.review_pairs_queued == 1
    assert pair.score == 0.55
    assert list(pair.features) == [
        "name_similarity",
        "key_overlap",
        "co_mention_overlap",
        "temporal_proximity",
        "same_source",
        "same_type",
    ]
    provenance_count = session.scalar(
        select(func.count())
        .select_from(ProvenanceORM)
        .where(ProvenanceORM.target_table == "review_pairs", ProvenanceORM.target_id == pair.id)
    )
    assert provenance_count == 2

    second = resolve_all(session, ontology, scorer=FixedScorer(0.55), review_margin=0.10)
    assert second.review_pairs_queued == 0
    assert len(session.scalars(select(ReviewPairORM)).all()) == 1


def test_accepting_queue_pair_is_append_only_and_applies_on_next_resolution(
    session, ontology, blob_store
):
    _make_mentions(session, blob_store, ontology, ["جورج دايوب", "جورج داوب"])
    resolve_all(session, ontology, scorer=FixedScorer(0.55), review_margin=0.10)
    session.flush()
    pair = session.scalar(select(ReviewPairORM))

    first = decide_review_pair(session, pair.id, True, "george", "same person")
    second = decide_review_pair(session, pair.id, False, "george", "corrected after review")
    third = decide_review_pair(session, pair.id, True, "george", "confirmed")
    session.flush()

    assert first.supersedes_id is None
    assert second.supersedes_id == first.id
    assert third.supersedes_id == second.id
    assert len(session.scalars(select(ResolutionDecisionORM)).all()) == 3
    assert len(list_review_items(session, status="accepted")) == 1

    stats = resolve_all(session, ontology, scorer=FixedScorer(0.55), review_margin=0.10)
    assert stats.human_constraints_applied == 1
    assert stats.entities_created == 1


def test_rejected_queue_pair_blocks_an_automatic_merge(session, ontology, blob_store):
    _make_mentions(session, blob_store, ontology, ["جورج دايوب", "جورج داوب"])
    resolve_all(session, ontology, scorer=FixedScorer(0.90), review_margin=0.40)
    session.flush()
    pair = session.scalar(select(ReviewPairORM))
    decide_review_pair(session, pair.id, False, "george")

    stats = resolve_all(session, ontology, scorer=FixedScorer(0.90), review_margin=0.40)
    assert stats.entities_created == 2


def test_manual_merge_joins_entities_even_without_a_blocking_pair(session, ontology, blob_store):
    _make_mentions(session, blob_store, ontology, ["دونالد ترامب", "بنيامين نتنياهو"])
    resolve_all(session, ontology)
    session.flush()
    entities = session.scalars(
        select(EntityORM).where(EntityORM.retracted.is_(False)).order_by(EntityORM.id)
    ).all()

    decision = record_manual_merge(session, entities[0].id, entities[1].id, "george")
    session.flush()
    assert decision.source == "manual_merge"
    provenance = session.scalars(
        select(ProvenanceORM).where(
            ProvenanceORM.target_table == "resolution_decisions",
            ProvenanceORM.target_id == decision.id,
        )
    ).all()
    assert len(provenance) == 2

    stats = resolve_all(session, ontology)
    assert stats.entities_created == 1


def test_manual_split_can_separate_identical_surface_forms(session, ontology, blob_store):
    mentions = _make_mentions(session, blob_store, ontology, ["سامر خليل", "سامر خليل"])
    resolve_all(session, ontology)
    session.flush()
    entity = session.scalar(select(EntityORM).where(EntityORM.retracted.is_(False)))

    record_manual_split(session, entity.id, mentions[0].id, mentions[1].id, "george")
    stats = resolve_all(session, ontology)

    assert stats.entities_created == 2
    live_entities = session.scalars(select(EntityORM).where(EntityORM.retracted.is_(False))).all()
    member_counts = [
        session.scalar(
            select(func.count())
            .select_from(EntityMentionORM)
            .where(EntityMentionORM.entity_id == row.id)
        )
        for row in live_entities
    ]
    assert sorted(member_counts) == [1, 1]


def test_export_contains_real_feature_vectors(session, ontology, blob_store, tmp_path):
    _make_mentions(session, blob_store, ontology, ["جورج دايوب", "جورج داوب"])
    resolve_all(session, ontology, scorer=FixedScorer(0.55), review_margin=0.10)
    session.flush()
    pair = session.scalar(select(ReviewPairORM))
    decide_review_pair(session, pair.id, True, "george")

    out = tmp_path / "review-labels.json"
    payload = export_review_labels(session, out)

    assert len(payload["pairs"]) == 1
    assert payload["pairs"][0]["same"] is True
    assert len(payload["pairs"][0]["feature_vector"]) == 6
    assert json.loads(out.read_text(encoding="utf-8")) == payload
