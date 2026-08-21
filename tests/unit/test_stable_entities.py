"""M4.2b stable entity observation and immutable generation history."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from scripts.show_stable_entity_history import build_parser
import src.pipeline.resolve_core as resolve_core
import src.resolve.stable_entities as stable_entities
from src.pipeline.resolve_core import resolve_all
from src.resolve.review import record_decision
from src.resolve.stable_entities import (
    StableEntityInvariantError,
    observe_live_entity_generation,
    stable_entity_history,
    stable_entity_snapshot_as_of,
)
from src.store.orm import (
    AppendOnlyStableEntityError,
    DocumentORM,
    EntityMentionORM,
    EntityORM,
    EvidenceIdentityORM,
    ExtractorVersionORM,
    MentionEvidenceIdentityORM,
    MentionORM,
    ProvenanceORM,
    ResolverGenerationORM,
    StableEntityLineageORM,
    StableEntityLineageEvidenceORM,
    StableEntityMembershipORM,
    StableEntityORM,
    StableEntityResolutionStateORM,
    StableEntitySnapshotORM,
)
from src.store.documents import load_document
from src.store.identity import ConstraintRemap
from src.store.provenance import create_document, create_mention, register_extractor_version


def _resolver_version(session):  # noqa: ANN001
    return register_extractor_version(
        session,
        "pair_scorer_resolver",
        "1.1.0",
        description="stable entity observation test resolver",
    )


def _mentions(session, ontology, blob_store, names: list[str]):  # noqa: ANN001
    extractor = register_extractor_version(session, "stable_entity_test", "1.0.0")
    result = []
    for index, name in enumerate(names):
        text = f"ذكر {name} في المصدر {index}"
        document = create_document(
            session,
            source="stable-entity-test",
            text=text,
            content_hash=f"stable-entity-content-{index}-{name}",
            blob_store=blob_store,
            url=f"https://example.test/stable-entity/{index}",
        )
        start = text.index(name)
        result.append(
            create_mention(
                session,
                document,
                name,
                start,
                start + len(name),
                "person",
                extractor,
                ontology,
                language="ar",
            )
        )
    return result


def _legacy_entity(
    session,
    mentions,
    canonical_name: str,
    *,
    resolver=None,
    record_provenance: bool = True,
):  # noqa: ANN001
    row = EntityORM(
        object_type="person",
        canonical_name=canonical_name,
        properties={"test": True},
    )
    session.add(row)
    session.flush()
    resolver = resolver or _resolver_version(session)
    for mention in mentions:
        session.add(EntityMentionORM(entity_id=row.id, mention_id=mention.id))
        if record_provenance:
            session.add(
                ProvenanceORM(
                    target_table="entities",
                    target_id=row.id,
                    document_id=mention.document_id,
                    mention_id=mention.id,
                    extractor_version_id=resolver.id,
                )
            )
    session.flush()
    return row


def _observe(session, resolver):  # noqa: ANN001
    return observe_live_entity_generation(
        session,
        resolver_extractor_version_id=resolver.id,
    )


def _stable_id_for_source_entity(session, generation_id: int, source_entity_id: int) -> int:  # noqa: ANN001
    value = session.scalar(
        select(StableEntitySnapshotORM.stable_entity_id).where(
            StableEntitySnapshotORM.generation_id == generation_id,
            StableEntitySnapshotORM.source_entity_id == source_entity_id,
        )
    )
    assert value is not None
    return value


def _evidence_id_and_fingerprint(session, mention_id: int) -> tuple[int, str]:  # noqa: ANN001
    value = session.execute(
        select(MentionEvidenceIdentityORM.evidence_identity_id, EvidenceIdentityORM.fingerprint)
        .join(
            EvidenceIdentityORM,
            EvidenceIdentityORM.id == MentionEvidenceIdentityORM.evidence_identity_id,
        )
        .where(MentionEvidenceIdentityORM.mention_id == mention_id)
    ).one()
    return value


def test_legacy_resolver_stays_legacy_until_observation_is_explicit(
    session, ontology, blob_store
):
    """The production resolver path does not silently activate M4.2b."""

    extractor = register_extractor_version(session, "gazetteer_extractor", "1.0.0")
    text = "قال دونالد ترامب اليوم"
    document = create_document(
        session,
        source="legacy-mode",
        text=text,
        content_hash="legacy-mode-content",
        blob_store=blob_store,
        url="https://example.test/legacy-mode",
    )
    name = "دونالد ترامب"
    start = text.index(name)
    create_mention(
        session,
        document,
        name,
        start,
        start + len(name),
        "person",
        extractor,
        ontology,
        language="ar",
    )

    resolve_all(session, ontology)

    assert session.scalar(select(func.count()).select_from(EntityORM)) == 1
    assert session.scalar(select(func.count()).select_from(StableEntityORM)) == 0
    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0


def test_observation_uses_durable_evidence_and_records_membership_provenance(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    legacy = _legacy_entity(session, mentions, "أحمد")
    observed = _observe(session, _resolver_version(session))

    snapshot = session.scalar(
        select(StableEntitySnapshotORM).where(
            StableEntitySnapshotORM.generation_id == observed.generation_id,
            StableEntitySnapshotORM.source_entity_id == legacy.id,
        )
    )
    assert snapshot is not None
    membership = session.scalar(
        select(StableEntityMembershipORM).where(
            StableEntityMembershipORM.snapshot_id == snapshot.id
        )
    )
    assert membership is not None
    assert membership.evidence_identity_id > 0
    assert membership.source_mention_id == mentions[0].id
    provenance = session.scalar(
        select(ProvenanceORM).where(
            ProvenanceORM.target_table == "stable_entity_memberships",
            ProvenanceORM.target_id == membership.id,
        )
    )
    assert provenance is not None
    assert provenance.mention_id == mentions[0].id
    assert provenance.document_id == mentions[0].document_id

    state = session.get(StableEntityResolutionStateORM, 1)
    assert state is not None
    assert state.active_generation_id == observed.generation_id
    assert state.max_generation_sequence == 1


def test_one_evidence_identity_cannot_belong_to_two_stable_snapshots_in_a_generation(
    session, ontology, blob_store
):
    left_mention, right_mention = _mentions(session, ontology, blob_store, ["أحمد", "محمد"])
    resolver = _resolver_version(session)
    _legacy_entity(session, [left_mention], "أحمد")
    _legacy_entity(session, [right_mention], "محمد")
    observed = _observe(session, resolver)
    memberships = list(
        session.scalars(
            select(StableEntityMembershipORM)
            .join(
                StableEntitySnapshotORM,
                StableEntitySnapshotORM.id == StableEntityMembershipORM.snapshot_id,
            )
            .where(StableEntitySnapshotORM.generation_id == observed.generation_id)
            .order_by(StableEntityMembershipORM.id)
        )
    )
    assert len(memberships) == 2

    session.add(
        StableEntityMembershipORM(
            snapshot_id=memberships[1].snapshot_id,
            generation_id=observed.generation_id,
            evidence_identity_id=memberships[0].evidence_identity_id,
            source_mention_id=memberships[0].source_mention_id,
        )
    )
    with pytest.raises(IntegrityError, match="generation_id, stable_entity_memberships"):
        session.flush()
    session.rollback()


def test_one_stable_uid_survives_three_unchanged_full_recomputes(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد")

    first = _observe(session, resolver)
    stable_id = _stable_id_for_source_entity(session, first.generation_id, legacy.id)
    stable_uid = session.get(StableEntityORM, stable_id).stable_uid

    for canonical_name in ("أحمد", "أحمد"):
        legacy.retracted = True
        legacy = _legacy_entity(session, mentions, canonical_name)
        observed = _observe(session, resolver)
        assert _stable_id_for_source_entity(session, observed.generation_id, legacy.id) == stable_id

    snapshots = list(
        session.scalars(
            select(StableEntitySnapshotORM)
            .where(StableEntitySnapshotORM.stable_entity_id == stable_id)
            .order_by(StableEntitySnapshotORM.generation_id)
        )
    )
    assert [snapshot.is_present for snapshot in snapshots] == [True, True, True]
    assert session.scalar(select(func.count()).select_from(StableEntityORM)) == 1
    history = stable_entity_history(session, stable_uid)
    assert history is not None
    assert history.current_target_uids == (stable_uid,)
    assert [row.relationship for row in history.lineage] == ["continued", "continued"]


def test_merge_keeps_deterministic_predecessor_and_old_uid_redirects_to_it(
    session, ontology, blob_store
):
    left_mention, right_mention = _mentions(session, ontology, blob_store, ["أحمد", "محمد"])
    resolver = _resolver_version(session)
    left = _legacy_entity(session, [left_mention], "أحمد")
    right = _legacy_entity(session, [right_mention], "محمد")
    first = _observe(session, resolver)
    left_stable_id = _stable_id_for_source_entity(session, first.generation_id, left.id)
    right_stable_id = _stable_id_for_source_entity(session, first.generation_id, right.id)
    left_uid = session.get(StableEntityORM, left_stable_id).stable_uid
    right_uid = session.get(StableEntityORM, right_stable_id).stable_uid

    left.retracted = True
    right.retracted = True
    merged = _legacy_entity(session, [left_mention, right_mention], "أحمد محمد")
    second = _observe(session, resolver)

    expected_survivor_uid = min(left_uid, right_uid)
    expected_survivor_id = (
        left_stable_id if left_uid == expected_survivor_uid else right_stable_id
    )
    expected_loser_id = right_stable_id if expected_survivor_id == left_stable_id else left_stable_id
    assert _stable_id_for_source_entity(session, second.generation_id, merged.id) == expected_survivor_id

    current = {
        row.stable_entity_id: row.is_present
        for row in session.scalars(
            select(StableEntitySnapshotORM).where(
                StableEntitySnapshotORM.generation_id == second.generation_id
            )
        )
    }
    assert current == {expected_survivor_id: True, expected_loser_id: False}
    lines = list(
        session.scalars(
            select(StableEntityLineageORM)
            .where(StableEntityLineageORM.generation_id == second.generation_id)
            .order_by(StableEntityLineageORM.relationship)
        )
    )
    assert {
        (line.from_stable_entity_id, line.to_stable_entity_id, line.relationship)
        for line in lines
    } == {
        (expected_survivor_id, expected_survivor_id, "continued"),
        (expected_loser_id, expected_survivor_id, "merged_into"),
    }

    lineage_evidence = list(
        session.scalars(
            select(StableEntityLineageEvidenceORM)
            .join(
                StableEntityLineageORM,
                StableEntityLineageORM.id == StableEntityLineageEvidenceORM.lineage_id,
            )
            .where(StableEntityLineageORM.generation_id == second.generation_id)
            .order_by(StableEntityLineageEvidenceORM.id)
        )
    )
    assert len(lineage_evidence) == 2
    expected_loser_mention = (
        right_mention if expected_loser_id == right_stable_id else left_mention
    )
    expected_loser_evidence_id, expected_loser_fingerprint = _evidence_id_and_fingerprint(
        session,
        expected_loser_mention.id,
    )
    merge_line = next(line for line in lines if line.relationship == "merged_into")
    merge_witnesses = [
        row for row in lineage_evidence if row.lineage_id == merge_line.id
    ]
    assert len(merge_witnesses) == 1
    assert merge_witnesses[0].evidence_identity_id == expected_loser_evidence_id
    source_membership = session.get(
        StableEntityMembershipORM, merge_witnesses[0].source_membership_id
    )
    assert source_membership is not None
    assert source_membership.source_mention_id == expected_loser_mention.id

    loser_uid = right_uid if expected_loser_id == right_stable_id else left_uid
    before = session.scalar(select(func.count()).select_from(StableEntitySnapshotORM))
    loser_history = stable_entity_history(session, loser_uid)
    after = session.scalar(select(func.count()).select_from(StableEntitySnapshotORM))
    assert loser_history is not None
    assert loser_history.current_target_uids == (expected_survivor_uid,)
    merge_view = next(view for view in loser_history.lineage if view.relationship == "merged_into")
    assert [witness.evidence_fingerprint for witness in merge_view.witnesses] == [
        expected_loser_fingerprint
    ]
    assert merge_view.witnesses[0].source_mention_id == expected_loser_mention.id
    assert before == after, "history reads must not mutate observed state"


def test_history_rejects_a_lineage_witness_from_the_wrong_generation(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    old = _legacy_entity(session, mentions, "أحمد")
    first = _observe(session, resolver)
    stable_id = _stable_id_for_source_entity(session, first.generation_id, old.id)
    stable_uid = session.get(StableEntityORM, stable_id).stable_uid
    prior_membership = session.scalar(
        select(StableEntityMembershipORM)
        .join(
            StableEntitySnapshotORM,
            StableEntitySnapshotORM.id == StableEntityMembershipORM.snapshot_id,
        )
        .where(StableEntitySnapshotORM.generation_id == first.generation_id)
    )
    assert prior_membership is not None

    old.retracted = True
    replacement = _legacy_entity(session, mentions, "أحمد")
    second = _observe(session, resolver)
    lineage = session.scalar(
        select(StableEntityLineageORM).where(
            StableEntityLineageORM.generation_id == second.generation_id
        )
    )
    assert lineage is not None
    witness = session.scalar(
        select(StableEntityLineageEvidenceORM).where(
            StableEntityLineageEvidenceORM.lineage_id == lineage.id
        )
    )
    assert witness is not None

    # SQLite has no production append-only trigger; use a direct bulk update
    # to model an external/corrupt write that still satisfies the composite
    # evidence FK.  The read contract must refuse its wrong-generation anchor.
    session.execute(
        update(StableEntityLineageEvidenceORM)
        .where(StableEntityLineageEvidenceORM.id == witness.id)
        .values(source_membership_id=prior_membership.id)
    )
    session.expire_all()
    with pytest.raises(StableEntityInvariantError, match="edge target's current generation"):
        stable_entity_history(session, stable_uid)

    assert replacement.id is not None


def test_split_keeps_largest_child_and_records_new_child_lineage(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد", "محمد", "علي"])
    resolver = _resolver_version(session)
    old = _legacy_entity(session, mentions, "أحمد محمد علي")
    first = _observe(session, resolver)
    retained_id = _stable_id_for_source_entity(session, first.generation_id, old.id)
    retained_uid = session.get(StableEntityORM, retained_id).stable_uid

    old.retracted = True
    largest_child = _legacy_entity(session, mentions[:2], "أحمد محمد")
    other_child = _legacy_entity(session, mentions[2:], "علي")
    second = _observe(session, resolver)

    assert _stable_id_for_source_entity(session, second.generation_id, largest_child.id) == retained_id
    other_id = _stable_id_for_source_entity(session, second.generation_id, other_child.id)
    assert other_id != retained_id
    lines = {
        (line.from_stable_entity_id, line.to_stable_entity_id, line.relationship)
        for line in session.scalars(
            select(StableEntityLineageORM).where(
                StableEntityLineageORM.generation_id == second.generation_id
            )
        )
    }
    assert lines == {
        (retained_id, retained_id, "continued"),
        (retained_id, other_id, "split_into"),
    }
    history = stable_entity_history(session, retained_uid)
    assert history is not None
    assert history.as_of_snapshot is not None
    assert history.as_of_snapshot.canonical_name == "أحمد محمد"
    assert history.current_target_uids == (retained_uid,)


def test_equal_split_tie_uses_durable_evidence_not_names_or_creation_order(
    session, ontology, blob_store
):
    """A mutable canonical name must never decide which child inherits a UID."""

    first_mention, second_mention = _mentions(session, ontology, blob_store, ["أحمد", "محمد"])
    resolver = _resolver_version(session)
    parent = _legacy_entity(session, [first_mention, second_mention], "الأصل القديم")
    first = _observe(session, resolver)
    retained_id = _stable_id_for_source_entity(session, first.generation_id, parent.id)

    first_evidence = _evidence_id_and_fingerprint(session, first_mention.id)
    second_evidence = _evidence_id_and_fingerprint(session, second_mention.id)
    lower_mention, higher_mention = sorted(
        ((first_mention, first_evidence), (second_mention, second_evidence)),
        key=lambda item: item[1][1],
    )

    parent.retracted = True
    # Create the higher-fingerprint child first and give it the lexically
    # smaller canonical name.  The old implementation would have retained the
    # UID here for text/order reasons; the evidence tuple chooses the lower
    # fingerprint child instead.
    higher_child = _legacy_entity(session, [higher_mention[0]], "ألف")
    lower_child = _legacy_entity(session, [lower_mention[0]], "ياء")
    second = _observe(session, resolver)

    assert _stable_id_for_source_entity(session, second.generation_id, lower_child.id) == retained_id
    assert _stable_id_for_source_entity(session, second.generation_id, higher_child.id) != retained_id


def test_later_absence_does_not_turn_an_old_split_edge_into_a_redirect(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد", "محمد", "علي"])
    resolver = _resolver_version(session)
    old = _legacy_entity(session, mentions, "أحمد محمد علي")
    first = _observe(session, resolver)
    retained_id = _stable_id_for_source_entity(session, first.generation_id, old.id)
    retained_uid = session.get(StableEntityORM, retained_id).stable_uid

    old.retracted = True
    primary = _legacy_entity(session, mentions[:2], "أحمد محمد")
    split_child = _legacy_entity(session, mentions[2:], "علي")
    _observe(session, resolver)

    # A later all-evidence retraction produces an absent snapshot.  The old
    # split edge remains historical explanation, not a claim that its sibling
    # is still a current redirect target.
    primary.retracted = True
    split_child.retracted = True
    _observe(session, resolver)

    history = stable_entity_history(session, retained_uid)
    assert history is not None
    assert history.as_of_snapshot is not None
    assert history.as_of_snapshot.is_present is False
    assert history.current_target_uids == ()


def test_absent_snapshot_makes_as_of_membership_truthful(session, ontology, blob_store):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد")
    first = _observe(session, resolver)
    stable_id = _stable_id_for_source_entity(session, first.generation_id, legacy.id)
    stable_uid = session.get(StableEntityORM, stable_id).stable_uid

    legacy.retracted = True
    second = _observe(session, resolver)

    before = stable_entity_snapshot_as_of(session, stable_uid, as_of_sequence=first.sequence)
    after = stable_entity_snapshot_as_of(session, stable_uid, as_of_sequence=second.sequence)
    assert before is not None and before.is_present is True
    assert len(before.memberships) == 1
    assert after is not None and after.is_present is False
    assert after.memberships == ()
    assert after.canonical_name == "أحمد"
    history = stable_entity_history(session, stable_uid)
    assert history is not None
    assert history.current_target_uids == ()


def test_observation_records_constraint_status_without_enforcing_it(
    session, ontology, blob_store
):
    left_mention, right_mention = _mentions(session, ontology, blob_store, ["أحمد", "محمد"])
    resolver = _resolver_version(session)
    _legacy_entity(session, [left_mention], "أحمد")
    _legacy_entity(session, [right_mention], "محمد")
    record_decision(
        session,
        left_mention.id,
        right_mention.id,
        "different",
        reviewer="george",
        source="test",
    )

    observed = _observe(session, resolver)

    assert observed.constraint_status_counts == {"remapped": 1}
    generation = session.get(ResolverGenerationORM, observed.generation_id)
    assert generation.constraint_status_counts == {"remapped": 1}
    assert generation.mode == "observe"
    assert session.scalar(select(func.count()).select_from(EntityORM)) == 2


def test_replacement_mentions_keep_the_stable_uid_and_remapped_constraint_status(
    session, ontology, blob_store
):
    """M4.2b follows M4.2a evidence IDs, never the raw mention generation."""

    left_mention, right_mention = _mentions(session, ontology, blob_store, ["أحمد", "محمد"])
    resolver = _resolver_version(session)
    old_left = _legacy_entity(session, [left_mention], "أحمد")
    old_right = _legacy_entity(session, [right_mention], "محمد")
    record_decision(
        session,
        left_mention.id,
        right_mention.id,
        "different",
        reviewer="george",
        source="test",
    )
    first = _observe(session, resolver)
    left_stable_id = _stable_id_for_source_entity(session, first.generation_id, old_left.id)
    right_stable_id = _stable_id_for_source_entity(session, first.generation_id, old_right.id)

    replacement_extractor = register_extractor_version(
        session,
        "stable_entity_test",
        "2.0.0",
    )

    def replacement_for(old_mention):  # noqa: ANN001
        document = load_document(session, old_mention.document_id, blob_store)
        return create_mention(
            session,
            document,
            old_mention.text,
            old_mention.start,
            old_mention.end,
            old_mention.object_type,
            replacement_extractor,
            ontology,
            language="ar",
        )

    replacement_left = replacement_for(left_mention)
    replacement_right = replacement_for(right_mention)
    old_left.retracted = True
    old_right.retracted = True
    new_left = _legacy_entity(session, [replacement_left], "أحمد")
    new_right = _legacy_entity(session, [replacement_right], "محمد")
    second = _observe(session, resolver)

    assert _stable_id_for_source_entity(session, second.generation_id, new_left.id) == left_stable_id
    assert _stable_id_for_source_entity(session, second.generation_id, new_right.id) == right_stable_id
    assert second.constraint_status_counts == {"remapped": 1}
    source_mentions = set(
        session.scalars(
            select(StableEntityMembershipORM.source_mention_id)
            .join(
                StableEntitySnapshotORM,
                StableEntitySnapshotORM.id == StableEntityMembershipORM.snapshot_id,
            )
            .where(StableEntitySnapshotORM.generation_id == second.generation_id)
        )
    )
    assert source_mentions == {replacement_left.id, replacement_right.id}


def test_observation_flushes_pending_legacy_retraction_when_autoflush_is_disabled(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد")
    first = _observe(session, resolver)
    stable_id = _stable_id_for_source_entity(session, first.generation_id, legacy.id)

    session.autoflush = False
    legacy.retracted = True
    second = _observe(session, resolver)

    snapshot = session.scalar(
        select(StableEntitySnapshotORM).where(
            StableEntitySnapshotORM.generation_id == second.generation_id,
            StableEntitySnapshotORM.stable_entity_id == stable_id,
        )
    )
    assert snapshot is not None
    assert snapshot.is_present is False


def test_observer_takes_the_shared_lock_before_flushing_pending_legacy_output(
    session, ontology, blob_store, monkeypatch
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد")
    legacy.retracted = True
    session.autoflush = False
    calls = []
    original_flush = session.flush

    def record_flush(*args, **kwargs):  # noqa: ANN001
        calls.append("flush")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(
        stable_entities,
        "acquire_resolution_output_lock",
        lambda locked_session: calls.append("lock"),
    )
    monkeypatch.setattr(session, "flush", record_flush)

    _observe(session, resolver)

    assert calls[:2] == ["lock", "flush"]


@pytest.mark.parametrize("retracted_row", ("mention", "document"))
def test_observation_fails_closed_for_retracted_live_legacy_evidence(
    session, ontology, blob_store, retracted_row
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    _legacy_entity(session, mentions, "أحمد")

    if retracted_row == "mention":
        session.get(MentionORM, mentions[0].id).retracted = True
    else:
        session.get(DocumentORM, mentions[0].document_id).retracted = True

    with pytest.raises(StableEntityInvariantError, match="retracted evidence"):
        _observe(session, resolver)

    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0


def test_observation_rejects_missing_or_mismatched_source_entity_provenance(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    _legacy_entity(session, mentions, "أحمد", record_provenance=False)

    with pytest.raises(StableEntityInvariantError, match="lacks one resolver provenance"):
        _observe(session, resolver)

    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0


def test_observation_rejects_mismatched_claimed_resolver_provenance(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    claimed_resolver = _resolver_version(session)
    other_resolver = register_extractor_version(session, "other_resolver", "1.0.0")
    _legacy_entity(session, mentions, "أحمد", resolver=other_resolver)

    with pytest.raises(StableEntityInvariantError, match="do not match claimed resolver"):
        _observe(session, claimed_resolver)

    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0


def test_late_observation_failure_rolls_back_the_entire_nested_generation(
    session, ontology, blob_store, monkeypatch
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    _legacy_entity(session, mentions, "أحمد")
    original_write_lineage = stable_entities._write_lineage

    def fail_late(*_args, **_kwargs):  # noqa: ANN001
        raise StableEntityInvariantError("injected late lineage failure")

    monkeypatch.setattr(stable_entities, "_write_lineage", fail_late)
    with pytest.raises(StableEntityInvariantError, match="injected late"):
        _observe(session, resolver)

    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0
    assert session.scalar(select(func.count()).select_from(StableEntityORM)) == 0
    assert session.get(StableEntityResolutionStateORM, 1) is None

    monkeypatch.setattr(stable_entities, "_write_lineage", original_write_lineage)
    recovered = _observe(session, resolver)
    assert recovered.sequence == 1


def test_resolve_all_retracts_empty_live_corpus_before_an_absent_observation(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    initial_resolution = resolve_all(session, ontology)
    assert initial_resolution.entities_created == 1
    first = _observe(session, resolver)
    stable_id = session.scalar(
        select(StableEntitySnapshotORM.stable_entity_id).where(
            StableEntitySnapshotORM.generation_id == first.generation_id,
            StableEntitySnapshotORM.is_present.is_(True),
        )
    )
    assert stable_id is not None

    session.get(DocumentORM, mentions[0].document_id).retracted = True
    empty_resolution = resolve_all(session, ontology)
    assert empty_resolution.mentions == 0
    assert empty_resolution.entities_retracted == 1
    assert session.scalar(
        select(func.count()).select_from(EntityORM).where(EntityORM.retracted.is_(False))
    ) == 0

    second = _observe(session, resolver)
    absent_snapshot = session.scalar(
        select(StableEntitySnapshotORM).where(
            StableEntitySnapshotORM.generation_id == second.generation_id,
            StableEntitySnapshotORM.stable_entity_id == stable_id,
        )
    )
    assert absent_snapshot is not None
    assert absent_snapshot.is_present is False


def test_legacy_writer_and_observer_both_take_the_shared_output_lock(
    session, ontology, monkeypatch
):
    observer_calls = []
    writer_calls = []

    monkeypatch.setattr(
        stable_entities,
        "acquire_resolution_output_lock",
        lambda locked_session: observer_calls.append(locked_session),
    )
    monkeypatch.setattr(
        resolve_core,
        "acquire_resolution_output_lock",
        lambda locked_session: writer_calls.append(locked_session),
    )

    resolver = _resolver_version(session)
    _observe(session, resolver)
    resolve_all(session, ontology)

    assert observer_calls == [session]
    assert writer_calls == [session]


def test_legacy_writer_locks_before_flushing_pending_entity_output(
    session, ontology, monkeypatch
):
    session.add(
        EntityORM(
            object_type="person",
            canonical_name="pending legacy entity",
            properties={},
        )
    )
    calls = []
    original_flush = session.flush

    def record_lock(locked_session):  # noqa: ANN001
        assert locked_session.autoflush is False
        calls.append("lock")

    def record_flush(*args, **kwargs):  # noqa: ANN001
        calls.append("flush")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(resolve_core, "acquire_resolution_output_lock", record_lock)
    monkeypatch.setattr(session, "flush", record_flush)

    resolve_all(session, ontology)

    assert calls[:2] == ["lock", "flush"]


def test_generation_digest_ignores_constraint_surrogate_ids_and_order():
    first = ConstraintRemap(
        constraint_id=1,
        source_decision_id=10,
        decision="same",
        left_evidence_fingerprint="sha256:a",
        right_evidence_fingerprint="sha256:b",
        left_mention_ids=(1,),
        right_mention_ids=(2,),
        status="conflict",
        reason="opposing_active_constraints",
        conflicting_constraint_ids=(2,),
    )
    second = ConstraintRemap(
        constraint_id=2,
        source_decision_id=20,
        decision="different",
        left_evidence_fingerprint="sha256:b",
        right_evidence_fingerprint="sha256:a",
        left_mention_ids=(2,),
        right_mention_ids=(1,),
        status="conflict",
        reason="opposing_active_constraints",
        conflicting_constraint_ids=(1,),
    )
    re_adopted_first = ConstraintRemap(
        constraint_id=101,
        source_decision_id=1_010,
        decision="same",
        left_evidence_fingerprint="sha256:a",
        right_evidence_fingerprint="sha256:b",
        left_mention_ids=(101,),
        right_mention_ids=(102,),
        status="conflict",
        reason="opposing_active_constraints",
        conflicting_constraint_ids=(202,),
    )
    re_adopted_second = ConstraintRemap(
        constraint_id=202,
        source_decision_id=2_020,
        decision="different",
        left_evidence_fingerprint="sha256:a",
        right_evidence_fingerprint="sha256:b",
        left_mention_ids=(102,),
        right_mention_ids=(101,),
        status="conflict",
        reason="opposing_active_constraints",
        conflicting_constraint_ids=(101,),
    )

    assert stable_entities._generation_input_digest([], [first, second], "1") == (
        stable_entities._generation_input_digest(
            [],
            [re_adopted_second, re_adopted_first],
            "1",
        )
    )


def test_enforcement_mode_is_refused_before_any_observed_generation(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    _legacy_entity(session, mentions, "أحمد")
    resolver = _resolver_version(session)

    with pytest.raises(StableEntityInvariantError, match="observe mode"):
        observe_live_entity_generation(
            session,
            resolver_extractor_version_id=resolver.id,
            mode="enforce",
        )

    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0
    assert session.scalar(select(func.count()).select_from(StableEntityORM)) == 0


def test_observation_refuses_a_live_entity_without_m42a_evidence_mapping(
    session, ontology, blob_store
):
    extractor = register_extractor_version(session, "unsafe_legacy", "1.0.0")
    text = "ذكر أحمد"
    document = create_document(
        session,
        source="unsafe-legacy",
        text=text,
        content_hash="unsafe-legacy-content",
        blob_store=blob_store,
        url="https://example.test/unsafe-legacy",
    )
    mention = MentionORM(
        document_id=document.id,
        text="أحمد",
        start_offset=text.index("أحمد"),
        end_offset=text.index("أحمد") + len("أحمد"),
        object_type="person",
        extractor_version_id=extractor.id,
    )
    session.add(mention)
    session.flush()
    _legacy_entity(session, [mention], "أحمد")

    with pytest.raises(StableEntityInvariantError, match="without durable evidence"):
        _observe(session, _resolver_version(session))

    state = session.get(StableEntityResolutionStateORM, 1)
    assert state is None, "failed observation must not leave a coordination row"
    assert session.scalar(select(func.count()).select_from(ResolverGenerationORM)) == 0


def test_stable_history_rows_reject_mutation_but_state_is_the_coordination_row(
    session, ontology, blob_store
):
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    observed = _observe(session, _resolver_version(session))
    snapshot = session.scalar(
        select(StableEntitySnapshotORM).where(
            StableEntitySnapshotORM.generation_id == observed.generation_id
        )
    )
    assert snapshot is None, "empty legacy output has no first stable snapshot"

    # Create a real observed snapshot after the empty generation.  The state
    # row is intentionally mutable so it can point at the complete generation.
    legacy = _legacy_entity(session, mentions, "أحمد")
    observed = _observe(
        session,
        session.scalar(
            select(ExtractorVersionORM).where(ExtractorVersionORM.name == "pair_scorer_resolver")
        ),
    )
    snapshot = session.scalar(
        select(StableEntitySnapshotORM).where(
            StableEntitySnapshotORM.generation_id == observed.generation_id,
            StableEntitySnapshotORM.source_entity_id == legacy.id,
        )
    )
    assert snapshot is not None
    snapshot.canonical_name = "rewritten"
    with pytest.raises(AppendOnlyStableEntityError, match="append-only"):
        session.flush()
    session.rollback()


def test_history_script_parser_is_separate_from_main_cli():
    args = build_parser().parse_args(
        ["4b7f2e20-c2c5-4c71-b9d3-3127db5035c2", "--as-of-sequence", "4"]
    )
    assert args.stable_uid == "4b7f2e20-c2c5-4c71-b9d3-3127db5035c2"
    assert args.as_of_sequence == 4


def test_state_updated_at_is_not_history_and_can_advance(session):
    """The singleton is a coordination cache, unlike immutable history rows."""

    state = StableEntityResolutionStateORM(
        id=1,
        active_generation_id=None,
        max_generation_sequence=0,
        updated_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    session.add(state)
    session.flush()
    state.updated_at = datetime(2026, 8, 22, tzinfo=timezone.utc)
    session.flush()
    assert session.get(StableEntityResolutionStateORM, 1).updated_at == state.updated_at
