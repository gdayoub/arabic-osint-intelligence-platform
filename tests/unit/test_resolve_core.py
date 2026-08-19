"""tests for the resolution pipeline step.

the headline test is the ترامب one. the live dashboard shows that name
split across four rows and this is the thing that is supposed to fix it.
"""

from __future__ import annotations

from sqlalchemy import select

from src.pipeline.resolve_core import (
    load_mention_contexts,
    pick_canonical_name,
    resolve_all,
)
from src.lang.arabic import ArabicAdapter
from src.store.orm import EntityMentionORM, EntityORM, ProvenanceORM
from src.store.provenance import create_document, create_mention, register_extractor_version

ar = ArabicAdapter()


def _document_with_mentions(session, blob_store, ontology, extractor, *, name, spellings, hash_prefix):
    """one document per spelling so mentions land in different documents,
    which is what the real corpus looks like."""
    made = []
    for i, spelling in enumerate(spellings):
        text = f"قال {spelling} في تصريح صحفي اليوم"
        document = create_document(
            session, source="AlJazeeraArabic", text=text,
            content_hash=f"{hash_prefix}-{i}", blob_store=blob_store,
            url=f"https://example.com/{hash_prefix}-{i}",
        )
        start = text.index(spelling)
        made.append(
            create_mention(
                session, document=document, text=spelling, start=start,
                end=start + len(spelling), object_type="person",
                extractor_version=extractor, ontology=ontology,
            )
        )
    return made


def test_spelling_variants_of_one_name_collapse_into_one_entity(session, ontology, blob_store):
    """the ترامب problem. four surface forms, one person, one entity."""
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="trump",
        spellings=["دونالد ترامب", "دونالد ترامب", "دونالد ترامب"],
        hash_prefix="trump",
    )
    session.flush()

    stats = resolve_all(session, ontology)

    assert stats.mentions == 3
    assert stats.entities_created == 1, "identical spellings must resolve to one entity"
    entity = session.scalar(select(EntityORM).where(EntityORM.retracted.is_(False)))
    assert entity.properties["mention_count"] == 3


def test_different_people_stay_separate(session, ontology, blob_store):
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="mixed", spellings=["دونالد ترامب", "بنيامين نتنياهو"], hash_prefix="mixed",
    )
    session.flush()

    stats = resolve_all(session, ontology)

    assert stats.entities_created == 2, "two different people must not merge"


def test_every_member_mention_gets_its_own_provenance(session, ontology, blob_store):
    """P1. an entity claiming three mentions has to justify all three and not
    just the one it was founded on."""
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="assad", spellings=["بشار الأسد", "بشار الأسد", "بشار الأسد"], hash_prefix="assad",
    )
    session.flush()

    resolve_all(session, ontology)
    entity = session.scalar(select(EntityORM).where(EntityORM.retracted.is_(False)))

    evidence = session.scalars(
        select(EntityMentionORM).where(EntityMentionORM.entity_id == entity.id)
    ).all()
    provenance = session.scalars(
        select(ProvenanceORM).where(
            ProvenanceORM.target_table == "entities", ProvenanceORM.target_id == entity.id
        )
    ).all()

    assert len(evidence) == 3
    assert len(provenance) == 3, "one provenance row per piece of evidence"


def test_rerunning_retracts_the_previous_generation(session, ontology, blob_store):
    """P6. the old answer stays on record instead of being deleted."""
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="x", spellings=["دونالد ترامب"], hash_prefix="rerun",
    )
    session.flush()

    resolve_all(session, ontology)
    session.flush()
    second = resolve_all(session, ontology)

    assert second.entities_retracted == 1
    live = session.scalars(select(EntityORM).where(EntityORM.retracted.is_(False))).all()
    retracted = session.scalars(select(EntityORM).where(EntityORM.retracted.is_(True))).all()
    assert len(live) == 1
    assert len(retracted) == 1, "the old entity is retracted, not deleted"
    assert "superseded by" in retracted[0].retracted_reason


def test_person_and_location_never_merge(session, ontology, blob_store):
    """a place and a person sharing a name is a coincidence, not identity."""
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    text = "زار سوريا في سوريا"
    document = create_document(
        session, source="s", text=text, content_hash="xt", blob_store=blob_store
    )
    create_mention(session, document, "سوريا", 4, 9, "person", extractor, ontology)
    create_mention(session, document, "سوريا", 13, 18, "location", extractor, ontology)
    session.flush()

    stats = resolve_all(session, ontology)
    assert stats.entities_created == 2


def test_reduction_ratio_is_reported(session, ontology, blob_store):
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="many",
        spellings=["دونالد ترامب", "بنيامين نتنياهو", "بشار الأسد", "علي خامنئي"],
        hash_prefix="many",
    )
    session.flush()

    stats = resolve_all(session, ontology)

    assert stats.full_pairs == 4 * 3 // 2
    assert 0.0 <= stats.reduction_ratio <= 1.0
    assert sum(size * count for size, count in stats.size_histogram.items()) == 4


def test_empty_corpus_is_safe(session, ontology):
    stats = resolve_all(session, ontology)
    assert stats.mentions == 0
    assert stats.entities_created == 0


def test_canonical_name_prefers_the_fuller_form():
    """الأسد appearing more often does not make it the better label. a
    reader seeing الأسد cannot tell which one it is."""
    raw = {1: "الأسد", 2: "الأسد", 3: "الأسد", 4: "بشار الأسد"}
    assert pick_canonical_name([1, 2, 3, 4], raw) == "بشار الأسد"


def test_context_excludes_the_mention_itself(session, ontology, blob_store):
    """a mention must not count its own name as co-occurring context."""
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    text = "التقى بشار الأسد مع علي خامنئي"
    document = create_document(
        session, source="s", text=text, content_hash="ctx", blob_store=blob_store
    )
    create_mention(session, document, "بشار الأسد", 6, 16, "person", extractor, ontology)
    create_mention(session, document, "علي خامنئي", 20, 30, "person", extractor, ontology)
    session.flush()

    contexts = load_mention_contexts(session, ar)
    first = list(contexts.values())[0]
    assert first.normalized_name not in first.co_mentions


def test_a_name_more_frequent_than_the_block_cap_still_resolves(session, ontology, blob_store):
    """the bug the first production run exposed.

    إيران appeared 476 times. all 476 normalized identically so blocking put
    them in one block, the block exceeded max_block_size, it was dropped as
    oversized, and the single most mentioned entity in the corpus came out as
    476 separate singletons. collapsing exact duplicates before blocking is
    what fixes it: identical strings of the same type need no model to merge.
    """
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    for i in range(40):
        text = "الوضع في إيران اليوم"
        document = create_document(
            session, source="s", text=text, content_hash=f"iran-{i}",
            blob_store=blob_store, url=f"https://example.com/iran-{i}",
        )
        create_mention(session, document, "إيران", 9, 14, "location", extractor, ontology)
    session.flush()

    # a cap far below the number of mentions. before the fix this dropped the
    # block and produced 40 singletons.
    stats = resolve_all(session, ontology, max_block_size=5)

    assert stats.mentions == 40
    assert stats.exact_duplicate_groups == 1, "40 identical strings are one distinct form"
    assert stats.entities_created == 1, "must be one entity, not 40 singletons"

    entity = session.scalar(select(EntityORM).where(EntityORM.retracted.is_(False)))
    assert entity.properties["mention_count"] == 40


def test_gazetteer_aliases_of_one_person_become_one_entity(session, ontology, blob_store):
    """the ترامب bug exactly as it appeared on the live dashboard.

    four surface forms sat as four separate rows totalling 575 mentions. the
    gazetteer already declares all four to be aliases of دونالد ترامب and
    resolution was ignoring that and trying to rediscover it with fuzzy
    matching, which failed because ترامب and ترمب share no trigrams.
    """
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="trump",
        spellings=["ترامب", "ترمب", "دونالد ترامب", "دونالد ترمب"],
        hash_prefix="alias",
    )
    session.flush()

    stats = resolve_all(session, ontology)

    assert stats.entities_created == 1, "four aliases of one person are one entity"
    entity = session.scalar(select(EntityORM).where(EntityORM.retracted.is_(False)))
    assert entity.canonical_name == "دونالد ترامب"
    assert len(entity.properties["surface_forms"]) == 4


def test_names_the_gazetteer_does_not_know_still_go_through_the_scorer(session, ontology, blob_store):
    """the dictionary handles what it knows. everything else still has to be
    resolved the hard way, so an unknown name must not silently vanish."""
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="unknown", spellings=["جان نويل بارو", "جان نويل بارو"], hash_prefix="unknown",
    )
    session.flush()

    stats = resolve_all(session, ontology)

    assert stats.entities_created == 1
    entity = session.scalar(select(EntityORM).where(EntityORM.retracted.is_(False)))
    assert entity.canonical_name == "جان نويل بارو"


def test_two_different_known_people_do_not_merge_via_the_gazetteer(session, ontology, blob_store):
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor,
        name="two", spellings=["ترامب", "نتنياهو"], hash_prefix="two-known",
    )
    session.flush()

    assert resolve_all(session, ontology).entities_created == 2
