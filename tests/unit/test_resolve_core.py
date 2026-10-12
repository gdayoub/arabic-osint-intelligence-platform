"""tests for the resolution pipeline step.

the headline test is the ترامب one. the live dashboard shows that name
split across four rows and this is the thing that is supposed to fix it.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import given, strategies as st
from sqlalchemy import select

from scripts.bench_resolution_partition import CountingConstraints, legacy_partition
from src.pipeline import resolve_core
from src.pipeline.resolve_core import (
    load_mention_contexts,
    pick_canonical_name,
    resolve_all,
)
from src.lang.arabic import ArabicAdapter
from src.resolve.review import ordered_pair, record_decision
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
                language="ar",
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
    create_mention(session, document, "سوريا", 4, 9, "person", extractor, ontology, language="ar")
    create_mention(session, document, "سوريا", 13, 18, "location", extractor, ontology, language="ar")
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
    create_mention(session, document, "بشار الأسد", 6, 16, "person", extractor, ontology, language="ar")
    create_mention(session, document, "علي خامنئي", 20, 30, "person", extractor, ontology, language="ar")
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
        create_mention(session, document, "إيران", 9, 14, "location", extractor, ontology, language="ar")
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


# The benchmark preserves the original helper as an executable reference.
# Tests assert output/work-count properties rather than machine timing.
@given(
    members=st.lists(st.integers(1, 100), unique=True, max_size=30),
    pairs=st.sets(
        st.tuples(st.integers(1, 100), st.integers(1, 100)).filter(lambda pair: pair[0] != pair[1]),
        max_size=50,
    ),
)
def test_partition_is_equivalent_to_original_for_empty_and_nonempty_constraints(members, pairs):
    cannot_links = {ordered_pair(*pair) for pair in pairs}
    original_members = list(members)
    assert resolve_core._partition_on_cannot_links(members, cannot_links) == legacy_partition(members, cannot_links)
    assert resolve_core._partition_on_cannot_links(members, set()) == legacy_partition(members, set())
    assert members == original_members


@pytest.mark.parametrize("size", [0, 1, 2, 500])
def test_empty_constraint_fast_path_performs_zero_pair_membership_checks(size):
    members = list(reversed(range(1, size + 1)))
    before, after = CountingConstraints(), CountingConstraints()
    assert resolve_core._partition_on_cannot_links(members, after) == legacy_partition(members, before)
    assert before.probes == size * (size - 1) // 2
    assert after.probes == 0


def test_nonempty_constraint_path_keeps_the_original_checks_and_partition_order():
    before = CountingConstraints({(1, 3), (2, 4)})
    after = CountingConstraints(before)
    members = [4, 3, 2, 1]
    assert resolve_core._partition_on_cannot_links(members, after) == legacy_partition(members, before)
    assert after.probes == before.probes > 0
    assert resolve_core._partition_on_cannot_links(members, after) == [[1, 2], [3, 4]]


def test_empty_constraint_fast_path_preserves_duplicate_mention_id_rejection():
    with pytest.raises(ValueError, match="two different mentions"):
        legacy_partition([1, 2, 1], set())
    with pytest.raises(ValueError, match="two different mentions"):
        resolve_core._partition_on_cannot_links([1, 2, 1], set())


@pytest.mark.parametrize("constrained", [False, True])
def test_full_resolution_output_and_evidence_match_original_partitioning(
    session, ontology, blob_store, monkeypatch, constrained
):
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    mentions = _document_with_mentions(
        session, blob_store, ontology, extractor, name="comparison",
        spellings=["دونالد ترامب", "ترامب", "ترمب", "بشار الأسد", "بشار الأسد"],
        hash_prefix="partition-equivalence",
    )
    session.flush()
    if constrained:
        record_decision(
            session, mentions[0].id, mentions[1].id, "different",
            reviewer="fixture", source="test",
        )

    def snapshot():
        entities = session.scalars(select(EntityORM).where(EntityORM.retracted.is_(False))).all()
        return sorted(
            (
                entity.canonical_name,
                entity.object_type,
                entity.properties["mention_count"],
                tuple(entity.properties["surface_forms"]),
                tuple(sorted(session.scalars(select(EntityMentionORM.mention_id).where(
                    EntityMentionORM.entity_id == entity.id
                )))),
                tuple(sorted(session.scalars(select(ProvenanceORM.mention_id).where(
                    ProvenanceORM.target_table == "entities", ProvenanceORM.target_id == entity.id
                )))),
            )
            for entity in entities
        )

    current_partition = resolve_core._partition_on_cannot_links
    monkeypatch.setattr(resolve_core, "_partition_on_cannot_links", legacy_partition)
    before = resolve_all(session, ontology)
    session.flush()
    expected = snapshot()
    monkeypatch.setattr(resolve_core, "_partition_on_cannot_links", current_partition)
    after = resolve_all(session, ontology)
    session.flush()
    assert snapshot() == expected
    for name in ("mentions", "candidate_pairs", "full_pairs", "matched_pairs", "entities_created",
                 "giant_components_split", "exact_duplicate_groups", "human_constraints_applied", "size_histogram"):
        assert getattr(after, name) == getattr(before, name)
    # Full recomputation and append-only history still happen on every run.
    assert after.entities_retracted == before.entities_created


def test_resolution_logs_fixed_phase_boundaries_and_only_counts(session, ontology, blob_store, caplog):
    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    _document_with_mentions(
        session, blob_store, ontology, extractor, name="private",
        spellings=["دونالد ترامب"] * 5, hash_prefix="private-content",
    )
    caplog.set_level("INFO", logger="pipeline.resolve_core")
    resolve_all(session, ontology)
    logs = [r.getMessage() for r in caplog.records if r.name == "pipeline.resolve_core"]
    expected = [
        "acquire_output_lock", "flush_pending", "initialize", "load_contexts", "load_mentions",
        "load_constraints", "group_mentions", "partition_groups", "remap_constraints", "blocking",
        "register_version", "scoring", "queue_review", "clustering", "retract_entities", "write_entities",
    ]
    assert len(logs) == len(expected) * 2
    for index, phase in enumerate(expected):
        assert logs[index * 2] == f"resolve_phase_started phase={phase}"
        assert logs[index * 2 + 1].startswith(f"resolve_phase_succeeded phase={phase} elapsed_seconds=")
    assert "mentions=5" in " ".join(logs)
    assert "دونالد ترامب" not in " ".join(logs)
    assert "private-content" not in " ".join(logs)


def test_resolution_phase_clock_is_monotonic_and_counters_are_allowlisted(monkeypatch, caplog):
    ticks = iter((40.0, 42.25))
    monkeypatch.setattr(resolve_core, "monotonic", lambda: next(ticks))
    caplog.set_level("INFO", logger="pipeline.resolve_core")
    with resolve_core._resolution_phase("load_contexts") as counters:
        counters["mentions"] = 12
        counters["article_text"] = "never log this body"
        counters["candidate_pairs"] = "never log this secret"
        counters["groups"] = -1
        counters["decisions"] = True
    assert "elapsed_seconds=2.250000 mentions=12" in caplog.text
    assert "never log" not in caplog.text
    assert "decisions=" not in caplog.text
    assert "groups=" not in caplog.text


def test_resolution_phase_failure_preserves_exception_without_logging_text(session, ontology, monkeypatch, caplog):
    secret = "token=private-error-body"
    original = RuntimeError(secret)
    caplog.set_level("INFO", logger="pipeline.resolve_core")

    def fail(*args):
        raise original

    monkeypatch.setattr(resolve_core, "load_mention_contexts", fail)
    with pytest.raises(RuntimeError) as raised:
        resolve_all(session, ontology)
    assert raised.value is original
    assert "resolve_phase_failed phase=load_contexts" in caplog.text
    assert "resolve_phase_succeeded phase=load_contexts" not in caplog.text
    assert secret not in caplog.text
    assert all(r.exc_info is None for r in caplog.records if r.name == "pipeline.resolve_core")


@pytest.mark.parametrize("commit_fails", [False, True])
def test_resolution_commit_log_only_succeeds_after_transaction_exit(monkeypatch, caplog, commit_fails):
    secret = "token=private-commit-error"
    original = RuntimeError(secret)
    stats = resolve_core.ResolveStats(entities_created=2, entities_retracted=1)
    caplog.set_level("INFO", logger="pipeline.resolve_core")
    monkeypatch.setattr(resolve_core, "resolve_all", lambda *args, **kwargs: stats)
    ticks = iter((100.0, 103.0))
    monkeypatch.setattr(resolve_core, "monotonic", lambda: next(ticks))

    @contextmanager
    def session_scope():
        yield object()
        assert "resolve_phase_started phase=commit" in caplog.text
        assert "resolve_phase_succeeded phase=commit" not in caplog.text
        if commit_fails:
            raise original

    monkeypatch.setattr(resolve_core, "get_core_session", session_scope)
    if commit_fails:
        with pytest.raises(RuntimeError) as raised:
            resolve_core.run_core_resolution()
        assert raised.value is original
        assert "resolve_phase_failed phase=commit elapsed_seconds=3.000000" in caplog.text
        assert "resolve_phase_succeeded phase=commit" not in caplog.text
    else:
        assert resolve_core.run_core_resolution() is stats
        assert "resolve_phase_succeeded phase=commit elapsed_seconds=3.000000" in caplog.text
        assert "entities_created=2 entities_retracted=1" in caplog.text
    assert secret not in caplog.text
