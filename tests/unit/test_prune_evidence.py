"""Pruning retracted entity evidence (ADR 0031).

Every resolve_all run retracts the previous generation and writes a new one.
These tests build two real generations and check that pruning removes only
the retracted generation's per-mention evidence.
"""

from __future__ import annotations

from sqlalchemy import func, select

from src.pipeline.prune_evidence import prune_retracted_entity_evidence
from src.pipeline.resolve_core import resolve_all
from src.store.orm import EntityMentionORM, EntityORM, MentionORM, ProvenanceORM
from src.store.provenance import create_document, create_mention, register_extractor_version


def _seed(session, blob_store, ontology):
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    for i, name in enumerate(["دونالد ترامب", "دونالد ترامب", "بيروت"]):
        text = f"قال {name} في تصريح صحفي اليوم"
        document = create_document(
            session, source="AlJazeeraArabic", text=text, content_hash=f"doc-{i}",
            blob_store=blob_store, url=f"https://example.com/{i}",
        )
        start = text.index(name)
        create_mention(
            session, document=document, text=name, start=start, end=start + len(name),
            object_type="location" if name == "بيروت" else "person",
            extractor_version=extractor, ontology=ontology, language="ar",
        )
    session.flush()


def _count(session, model, *where):
    return session.scalar(select(func.count()).select_from(model).where(*where))


def _entity_evidence(session, retracted: bool) -> tuple[int, int]:
    ids = select(EntityORM.id).where(EntityORM.retracted.is_(retracted))
    links = _count(session, EntityMentionORM, EntityMentionORM.entity_id.in_(ids))
    prov = _count(
        session, ProvenanceORM,
        ProvenanceORM.target_table == "entities", ProvenanceORM.target_id.in_(ids),
    )
    return links, prov


def _two_generations(session, blob_store, ontology):
    _seed(session, blob_store, ontology)
    resolve_all(session, ontology)
    resolve_all(session, ontology)
    session.flush()


def test_dry_run_counts_but_deletes_nothing(session, ontology, blob_store):
    _two_generations(session, blob_store, ontology)
    before = _entity_evidence(session, retracted=True)
    assert before[0] > 0 and before[1] > 0

    stats = prune_retracted_entity_evidence(session, apply=False)

    assert (stats.entity_mentions, stats.provenance) == before
    assert stats.retracted_entities > 0
    assert _entity_evidence(session, retracted=True) == before


def test_apply_removes_only_retracted_evidence(session, ontology, blob_store):
    _two_generations(session, blob_store, ontology)
    live_before = _entity_evidence(session, retracted=False)
    entities_before = _count(session, EntityORM)
    mention_prov_before = _count(session, ProvenanceORM, ProvenanceORM.target_table == "mentions")

    stats = prune_retracted_entity_evidence(session, apply=True, batch_entities=1)

    assert stats.applied
    assert _entity_evidence(session, retracted=True) == (0, 0)
    assert _entity_evidence(session, retracted=False) == live_before, "live evidence must be untouched"
    assert _count(session, EntityORM) == entities_before, "retracted entity rows stay on record"
    assert _count(session, MentionORM) == 3
    assert _count(session, ProvenanceORM, ProvenanceORM.target_table == "mentions") == mention_prov_before


def test_prune_is_idempotent_and_resolution_still_works(session, ontology, blob_store):
    _two_generations(session, blob_store, ontology)
    prune_retracted_entity_evidence(session, apply=True)

    again = prune_retracted_entity_evidence(session, apply=True)
    assert (again.entity_mentions, again.provenance) == (0, 0)

    stats = resolve_all(session, ontology)
    assert stats.entities_created > 0


def test_empty_database_is_safe(session):
    stats = prune_retracted_entity_evidence(session, apply=True)
    assert (stats.retracted_entities, stats.entity_mentions, stats.provenance) == (0, 0, 0)
