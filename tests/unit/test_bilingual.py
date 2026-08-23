"""The private bilingual derived-data foundation (migration 0006).

Covers the six invariants ADR 0031 exists to protect: duplicate content
addresses reuse one artifact, a variant anchored to a membership the active
generation has moved past drops out of the active view, a translation use
cannot cross generation/evidence identity, artifacts are immutable, an
empty translation output is a real stored fact rather than a cache miss,
and romanization never creates (or is confused with) a translation
artifact.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.resolve.stable_entities import observe_live_entity_generation
from src.store.bilingual import (
    BilingualInvariantError,
    active_translation_uses,
    active_variants,
    add_verified_alias_variant,
    get_or_create_translation_artifact,
    get_translation_artifact,
    record_translation_use,
    romanize_stable_entity,
    settings_sha256,
    source_text_sha256,
)
from src.store.orm import (
    AppendOnlyBilingualError,
    EntityMentionORM,
    EntityORM,
    ExtractorVersionORM,
    ProvenanceORM,
    StableEntityMembershipORM,
    StableEntityORM,
    StableEntityResolutionStateORM,
    StableEntitySnapshotORM,
    StableEntityVariantORM,
    TranslationArtifactORM,
    TranslationUseORM,
)
from src.store.provenance import create_document, create_mention, register_extractor_version


def _resolver_version(session):  # noqa: ANN001
    return register_extractor_version(
        session, "pair_scorer_resolver", "1.1.0", description="bilingual test resolver"
    )


def _mentions(session, ontology, blob_store, names: list[str]):  # noqa: ANN001
    extractor = register_extractor_version(session, "bilingual_test", "1.0.0")
    result = []
    for index, name in enumerate(names):
        text = f"ذكر {name} في المصدر {index}"
        document = create_document(
            session,
            source="bilingual-test",
            text=text,
            content_hash=f"bilingual-content-{index}-{name}",
            blob_store=blob_store,
            url=f"https://example.test/bilingual/{index}",
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


def _legacy_entity(session, mentions, canonical_name: str, *, resolver=None):  # noqa: ANN001
    row = EntityORM(object_type="person", canonical_name=canonical_name, properties={})
    session.add(row)
    session.flush()
    resolver = resolver or _resolver_version(session)
    for mention in mentions:
        session.add(EntityMentionORM(entity_id=row.id, mention_id=mention.id))
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
    return observe_live_entity_generation(session, resolver_extractor_version_id=resolver.id)


def _membership_for(session, generation_id: int, source_entity_id: int):  # noqa: ANN001
    """The one durable membership a legacy entity's mentions produced."""
    stable_id = session.scalar(
        select(StableEntitySnapshotORM.stable_entity_id).where(
            StableEntitySnapshotORM.generation_id == generation_id,
            StableEntitySnapshotORM.source_entity_id == source_entity_id,
        )
    )
    assert stable_id is not None
    stable_entity = session.get(StableEntityORM, stable_id)
    membership = session.scalar(
        select(StableEntityMembershipORM)
        .join(
            StableEntitySnapshotORM,
            StableEntitySnapshotORM.id == StableEntityMembershipORM.snapshot_id,
        )
        .where(
            StableEntitySnapshotORM.generation_id == generation_id,
            StableEntitySnapshotORM.stable_entity_id == stable_id,
        )
    )
    assert membership is not None
    return stable_entity, membership


def _build_two_entities(session, ontology, blob_store):  # noqa: ANN001
    """Two independently-resolved stable entities in the same generation."""
    mentions = _mentions(session, ontology, blob_store, ["أحمد", "محمد"])
    resolver = _resolver_version(session)
    first_legacy = _legacy_entity(session, [mentions[0]], "أحمد", resolver=resolver)
    second_legacy = _legacy_entity(session, [mentions[1]], "محمد", resolver=resolver)
    generation = _observe(session, resolver)
    first_entity, first_membership = _membership_for(
        session, generation.generation_id, first_legacy.id
    )
    second_entity, second_membership = _membership_for(
        session, generation.generation_id, second_legacy.id
    )
    return (first_entity, first_membership), (second_entity, second_membership), resolver


# ---------------------------------------------------------------------------
# 1. Duplicate artifact reuse
# ---------------------------------------------------------------------------


def test_duplicate_translation_artifact_request_reuses_the_same_row(session):  # noqa: ANN001
    kwargs = dict(
        source_text="مرحبا",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="hello",
    )
    first = get_or_create_translation_artifact(session, **kwargs)
    second = get_or_create_translation_artifact(session, **kwargs)

    assert first.id == second.id
    rows = session.scalars(select(TranslationArtifactORM)).all()
    assert len(rows) == 1


def test_different_settings_hash_creates_a_distinct_artifact(session):  # noqa: ANN001
    base = dict(
        source_text="مرحبا",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="hello",
    )
    plain = get_or_create_translation_artifact(session, **base)
    formal = get_or_create_translation_artifact(
        session, settings={"formality": "formal"}, **base
    )
    assert plain.id != formal.id
    rows = session.scalars(select(TranslationArtifactORM)).all()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 2. Retracted membership exclusion
# ---------------------------------------------------------------------------


def test_retracted_membership_excludes_variant_from_active_view(session, ontology, blob_store):  # noqa: ANN001
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد", resolver=resolver)
    first_generation = _observe(session, resolver)
    stable_entity, membership = _membership_for(
        session, first_generation.generation_id, legacy.id
    )

    variant = add_verified_alias_variant(
        session,
        stable_entity=stable_entity,
        membership=membership,
        surface_text="Ahmad",
        language="en",
    )
    assert active_variants(session, stable_entity.id) == [variant]

    # Retract the live legacy evidence and observe again: the new generation
    # has no membership for this stable entity, so the resolution pointer
    # moves past the generation that produced the variant.
    legacy.retracted = True
    session.flush()
    _observe(session, resolver)

    assert active_variants(session, stable_entity.id) == []
    # The row itself is never deleted -- append-only history survives.
    assert session.get(StableEntityVariantORM, variant.id) is not None


def test_retracted_membership_excludes_translation_use_from_active_view(
    session, ontology, blob_store
):  # noqa: ANN001
    """active_translation_uses() mirrors active_variants() for TranslationUse."""
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد", resolver=resolver)
    first_generation = _observe(session, resolver)
    stable_entity, membership = _membership_for(
        session, first_generation.generation_id, legacy.id
    )

    artifact = get_or_create_translation_artifact(
        session,
        source_text="أحمد",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="Ahmad",
    )
    use = record_translation_use(
        session,
        artifact=artifact,
        evidence_identity_id=membership.evidence_identity_id,
        membership=membership,
    )
    assert active_translation_uses(session, stable_entity.id) == [use]

    legacy.retracted = True
    session.flush()
    _observe(session, resolver)

    assert active_translation_uses(session, stable_entity.id) == []
    # The row itself is never deleted -- append-only history survives.
    assert session.get(TranslationUseORM, use.id) is not None


def test_evidence_only_translation_use_has_no_generation_to_retract_from(
    session, ontology, blob_store
):  # noqa: ANN001
    """A use with no membership at all is a permanent evidence-span fact,
    not a claim about a stable entity, so it is out of scope for
    active_translation_uses() (which is scoped by stable_entity_id) and is
    never excluded the way a membership-anchored use can be."""
    mentions = _mentions(session, ontology, blob_store, ["أحمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "أحمد", resolver=resolver)
    generation = _observe(session, resolver)
    _, membership = _membership_for(session, generation.generation_id, legacy.id)

    artifact = get_or_create_translation_artifact(
        session,
        source_text="أحمد",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="Ahmad",
    )
    use = record_translation_use(
        session,
        artifact=artifact,
        evidence_identity_id=membership.evidence_identity_id,
    )
    assert use.source_membership_id is None
    # Retracting the entity doesn't touch this use -- it never claimed to
    # be anchored to a stable entity in the first place.
    legacy.retracted = True
    session.flush()
    _observe(session, resolver)
    assert session.get(TranslationUseORM, use.id) is not None


# ---------------------------------------------------------------------------
# 3. Invalid cross-generation use
# ---------------------------------------------------------------------------


def test_translation_use_rejects_a_membership_from_different_evidence(
    session, ontology, blob_store
):  # noqa: ANN001
    (_, first_membership), (_, second_membership), _ = _build_two_entities(
        session, ontology, blob_store
    )
    artifact = get_or_create_translation_artifact(
        session,
        source_text="أحمد",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="Ahmad",
    )

    with pytest.raises(BilingualInvariantError, match="cannot cross evidence"):
        record_translation_use(
            session,
            artifact=artifact,
            evidence_identity_id=first_membership.evidence_identity_id,
            membership=second_membership,
        )


def test_database_rejects_a_hand_written_cross_generation_translation_use(
    session, ontology, blob_store
):  # noqa: ANN001
    """Defense in depth: even bypassing the repository check, the composite
    foreign key refuses a membership/evidence pair that does not match."""
    (_, first_membership), (_, second_membership), _ = _build_two_entities(
        session, ontology, blob_store
    )
    artifact = get_or_create_translation_artifact(
        session,
        source_text="أحمد",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="Ahmad",
    )

    session.add(
        TranslationUseORM(
            artifact_id=artifact.id,
            evidence_identity_id=first_membership.evidence_identity_id,
            source_membership_id=second_membership.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_variant_rejects_a_membership_from_a_different_stable_entity(
    session, ontology, blob_store
):  # noqa: ANN001
    """A variant's membership/evidence pair can satisfy the composite FK
    while still belonging to the wrong stable entity -- the FK only proves
    membership<->evidence, not membership<->stable_entity. _add_variant()
    must catch that mismatch itself."""
    (first_entity, _), (_, second_membership), _ = _build_two_entities(
        session, ontology, blob_store
    )

    with pytest.raises(BilingualInvariantError, match="belongs to stable entity"):
        add_verified_alias_variant(
            session,
            stable_entity=first_entity,
            membership=second_membership,
            surface_text="Mohammed",
            language="en",
        )
    assert session.scalars(select(StableEntityVariantORM)).all() == []


# ---------------------------------------------------------------------------
# 4. Immutable artifact fields
# ---------------------------------------------------------------------------


def test_translation_artifact_fields_are_immutable(session):  # noqa: ANN001
    artifact = get_or_create_translation_artifact(
        session,
        source_text="مرحبا",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="hello",
    )
    artifact.output_text = "goodbye"
    with pytest.raises(AppendOnlyBilingualError, match="append-only"):
        session.flush()
    session.rollback()


def test_translation_artifact_delete_is_rejected(session):  # noqa: ANN001
    artifact = get_or_create_translation_artifact(
        session,
        source_text="مرحبا",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="hello",
    )
    session.delete(artifact)
    with pytest.raises(AppendOnlyBilingualError, match="append-only"):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# 5. Empty English output
# ---------------------------------------------------------------------------


def test_empty_translation_output_is_stored_and_distinguishable_from_uncached(session):  # noqa: ANN001
    lookup_kwargs = dict(
        source_text="⁘",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
    )
    assert get_translation_artifact(session, **lookup_kwargs) is None

    artifact = get_or_create_translation_artifact(session, output_text="", **lookup_kwargs)
    assert artifact.output_text == ""

    fetched = get_translation_artifact(session, **lookup_kwargs)
    assert fetched is not None
    assert fetched.id == artifact.id
    assert fetched.output_text == ""


def test_none_output_text_is_rejected_outright(session):  # noqa: ANN001
    with pytest.raises(BilingualInvariantError, match="output_text"):
        get_or_create_translation_artifact(
            session,
            source_text="مرحبا",
            source_lang="ar",
            target_lang="en",
            provider="deepl",
            provider_version="2.0",
            output_text=None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# 6. Romanization distinct from translation
# ---------------------------------------------------------------------------


def test_romanization_creates_no_translation_artifact(session, ontology, blob_store):  # noqa: ANN001
    mentions = _mentions(session, ontology, blob_store, ["محمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "محمد", resolver=resolver)
    generation = _observe(session, resolver)
    stable_entity, membership = _membership_for(session, generation.generation_id, legacy.id)

    variants = romanize_stable_entity(
        session, stable_entity=stable_entity, membership=membership, arabic_text="محمد"
    )
    assert variants
    assert all(variant.variant_type == "romanization" for variant in variants)

    assert session.scalars(select(TranslationArtifactORM)).all() == []
    assert session.scalars(select(TranslationUseORM)).all() == []

    romanizations = active_variants(session, stable_entity.id, variant_type="romanization")
    assert {v.id for v in romanizations} == {v.id for v in variants}
    assert active_variants(session, stable_entity.id, variant_type="verified_alias") == []


def test_romanization_and_translation_of_the_same_name_stay_separately_queryable(
    session, ontology, blob_store
):  # noqa: ANN001
    mentions = _mentions(session, ontology, blob_store, ["محمد"])
    resolver = _resolver_version(session)
    legacy = _legacy_entity(session, mentions, "محمد", resolver=resolver)
    generation = _observe(session, resolver)
    stable_entity, membership = _membership_for(session, generation.generation_id, legacy.id)

    romanize_stable_entity(
        session, stable_entity=stable_entity, membership=membership, arabic_text="محمد"
    )
    artifact = get_or_create_translation_artifact(
        session,
        source_text="محمد",
        source_lang="ar",
        target_lang="en",
        provider="deepl",
        provider_version="2.0",
        output_text="Mohammed",
    )
    record_translation_use(
        session,
        artifact=artifact,
        evidence_identity_id=membership.evidence_identity_id,
        membership=membership,
    )

    romanizations = active_variants(session, stable_entity.id, variant_type="romanization")
    assert romanizations
    assert all(v.variant_type != "translation" for v in romanizations)
    assert session.scalars(select(TranslationArtifactORM)).all() == [artifact]


def test_settings_sha256_treats_none_and_empty_mapping_as_the_same_fact():  # noqa: ANN001
    assert settings_sha256(None) == settings_sha256({})


def test_source_text_sha256_is_deterministic():  # noqa: ANN001
    assert source_text_sha256("مرحبا") == source_text_sha256("مرحبا")
    assert source_text_sha256("مرحبا") != source_text_sha256("وداعا")
