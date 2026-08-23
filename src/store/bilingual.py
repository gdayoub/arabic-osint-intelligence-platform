"""The private bilingual derived-data foundation (migration 0006).

Three concepts, kept deliberately separate so translation, romanization, and
human-verified alias provenance never collapse into one ambiguous
"alternate name" bucket -- see docs/adr/0031-private-bilingual-derived-data-foundation.md.

- ``TranslationArtifact``: an immutable, content-addressed machine
  translation output.  Content address = source text hash + source
  language + target language + provider + provider version + settings
  hash.  Two requests with the same address always resolve to the same
  row -- nothing already on file is ever re-translated, and nothing on
  file is ever silently overwritten with a different output for the same
  address.  An empty translation result (``output_text == ""``) is a
  normal, storable outcome, distinct from "never attempted" (no row).
- ``TranslationUse``: one immutable pointer from a ``TranslationArtifact``
  to the exact evidence span -- and, when the translated surface came from
  a stable entity, the exact durable membership -- it was applied to.  A
  use can never point its evidence and membership at two different
  generations or identities: the DB enforces this with the same composite
  foreign key pattern ``StableEntityLineageEvidenceORM`` already uses.
- ``StableEntityVariant``: a durable alternate surface form attached to a
  stable entity.  ``variant_type`` is either ``"romanization"``
  (deterministic, produced locally by ``ArabicAdapter.romanize()``, no
  external provider, no ``TranslationArtifact`` involved) or
  ``"verified_alias"`` (a human-confirmed alternate name).  It is never
  ``"translation"`` -- a translated surface belongs on
  ``TranslationArtifact``/``TranslationUse`` instead.

All three tables are append-only, like the rest of the stable-entity
history (Global Constraint 7): repair by writing a new row, never by
mutating one that exists.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lang.arabic import ArabicAdapter
from src.store.orm import (
    STABLE_ENTITY_VARIANT_TYPES,
    StableEntityMembershipORM,
    StableEntityORM,
    StableEntityResolutionStateORM,
    StableEntitySnapshotORM,
    StableEntityVariantORM,
    TranslationArtifactORM,
    TranslationUseORM,
)

_ROMANIZATION_LANGUAGE = "ar-Latn"
_DEFAULT_ROMANIZATION_CANDIDATES = 3


class BilingualInvariantError(ValueError):
    """The bilingual layer cannot safely represent the supplied derived fact."""


def source_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def settings_sha256(settings: Mapping[str, Any] | None) -> str:
    """Canonical, order-independent hash of a provider settings payload.

    ``None`` and ``{}`` both hash to the same value -- "no settings" is one
    fact, not two -- which keeps the content address stable for providers
    that don't take options.
    """
    encoded = json.dumps(
        dict(settings or {}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BilingualInvariantError(f"{field} must be a non-empty string")
    return value


def get_or_create_translation_artifact(
    session: Session,
    *,
    source_text: str,
    source_lang: str,
    target_lang: str,
    provider: str,
    provider_version: str,
    output_text: str,
    settings: Mapping[str, Any] | None = None,
) -> TranslationArtifactORM:
    """Get-or-create the artifact for one content address.

    ``output_text`` must be a ``str`` -- pass ``""`` for a genuinely empty
    translation result.  ``None`` is rejected outright, so "the provider
    returned nothing" can never be mistaken for "not translated yet" (which
    is simply the absence of a row).
    """
    _require_non_empty(source_text, "source_text")
    _require_non_empty(source_lang, "source_lang")
    _require_non_empty(target_lang, "target_lang")
    _require_non_empty(provider, "provider")
    _require_non_empty(provider_version, "provider_version")
    if not isinstance(output_text, str):
        raise BilingualInvariantError(
            "output_text must be a string; use '' for an empty translation, not None"
        )

    text_hash = source_text_sha256(source_text)
    settings_hash = settings_sha256(settings)

    existing = session.scalar(
        select(TranslationArtifactORM).where(
            TranslationArtifactORM.source_text_sha256 == text_hash,
            TranslationArtifactORM.source_lang == source_lang,
            TranslationArtifactORM.target_lang == target_lang,
            TranslationArtifactORM.provider == provider,
            TranslationArtifactORM.provider_version == provider_version,
            TranslationArtifactORM.settings_sha256 == settings_hash,
        )
    )
    if existing is not None:
        if existing.output_text != output_text:
            raise BilingualInvariantError(
                "translation artifact content address already resolves to a "
                "different output; artifacts are immutable, so a genuinely "
                "different result needs a new provider_version or settings"
            )
        return existing

    row = TranslationArtifactORM(
        source_text_sha256=text_hash,
        source_lang=source_lang,
        target_lang=target_lang,
        provider=provider,
        provider_version=provider_version,
        settings_sha256=settings_hash,
        output_text=output_text,
    )
    session.add(row)
    session.flush()
    return row


def get_translation_artifact(
    session: Session,
    *,
    source_text: str,
    source_lang: str,
    target_lang: str,
    provider: str,
    provider_version: str,
    settings: Mapping[str, Any] | None = None,
) -> TranslationArtifactORM | None:
    """Look up a translation artifact by content address, without writing."""
    text_hash = source_text_sha256(source_text)
    settings_hash = settings_sha256(settings)
    return session.scalar(
        select(TranslationArtifactORM).where(
            TranslationArtifactORM.source_text_sha256 == text_hash,
            TranslationArtifactORM.source_lang == source_lang,
            TranslationArtifactORM.target_lang == target_lang,
            TranslationArtifactORM.provider == provider,
            TranslationArtifactORM.provider_version == provider_version,
            TranslationArtifactORM.settings_sha256 == settings_hash,
        )
    )


def record_translation_use(
    session: Session,
    *,
    artifact: TranslationArtifactORM,
    evidence_identity_id: int,
    membership: StableEntityMembershipORM | None = None,
) -> TranslationUseORM:
    """Record where a translation artifact's output was applied.

    ``membership``, when given, must be the exact membership that witnesses
    ``evidence_identity_id`` -- the same rule the composite foreign key
    enforces at the DB layer, checked here first for a clear application
    error rather than a raw IntegrityError.
    """
    if membership is not None and membership.evidence_identity_id != evidence_identity_id:
        raise BilingualInvariantError(
            f"membership {membership.id} witnesses evidence "
            f"{membership.evidence_identity_id}, not {evidence_identity_id}; "
            "a translation use cannot cross evidence/generation identity"
        )

    existing = session.scalar(
        select(TranslationUseORM).where(
            TranslationUseORM.artifact_id == artifact.id,
            TranslationUseORM.evidence_identity_id == evidence_identity_id,
            TranslationUseORM.source_membership_id
            == (membership.id if membership is not None else None),
        )
    )
    if existing is not None:
        return existing

    row = TranslationUseORM(
        artifact_id=artifact.id,
        evidence_identity_id=evidence_identity_id,
        source_membership_id=membership.id if membership is not None else None,
    )
    session.add(row)
    session.flush()
    return row


def _membership_stable_entity_id(session: Session, membership: StableEntityMembershipORM) -> int | None:
    """The stable entity id the membership's own snapshot actually belongs to.

    ``StableEntityMembershipORM`` carries ``snapshot_id``/``generation_id``
    but not a denormalized ``stable_entity_id`` of its own, so the only
    truthful way to answer "whose membership is this" is to look at the
    snapshot it was written under.
    """
    return session.scalar(
        select(StableEntitySnapshotORM.stable_entity_id).where(
            StableEntitySnapshotORM.id == membership.snapshot_id
        )
    )


def _add_variant(
    session: Session,
    *,
    stable_entity: StableEntityORM,
    membership: StableEntityMembershipORM,
    variant_type: str,
    surface_text: str,
    language: str,
) -> StableEntityVariantORM:
    if variant_type not in STABLE_ENTITY_VARIANT_TYPES:
        raise BilingualInvariantError(
            f"variant_type must be one of {STABLE_ENTITY_VARIANT_TYPES}, got {variant_type!r}"
        )
    _require_non_empty(surface_text, "surface_text")
    _require_non_empty(language, "language")

    membership_stable_entity_id = _membership_stable_entity_id(session, membership)
    if membership_stable_entity_id != stable_entity.id:
        raise BilingualInvariantError(
            f"membership {membership.id} belongs to stable entity "
            f"{membership_stable_entity_id}, not {stable_entity.id}; a variant "
            "cannot be attributed to a stable entity its membership doesn't witness"
        )

    existing = session.scalar(
        select(StableEntityVariantORM).where(
            StableEntityVariantORM.stable_entity_id == stable_entity.id,
            StableEntityVariantORM.variant_type == variant_type,
            StableEntityVariantORM.surface_text == surface_text,
            StableEntityVariantORM.language == language,
        )
    )
    if existing is not None:
        return existing

    row = StableEntityVariantORM(
        stable_entity_id=stable_entity.id,
        variant_type=variant_type,
        surface_text=surface_text,
        language=language,
        source_membership_id=membership.id,
        evidence_identity_id=membership.evidence_identity_id,
    )
    session.add(row)
    session.flush()
    return row


def add_verified_alias_variant(
    session: Session,
    *,
    stable_entity: StableEntityORM,
    membership: StableEntityMembershipORM,
    surface_text: str,
    language: str,
) -> StableEntityVariantORM:
    """Record a human-confirmed alternate name for a stable entity."""
    return _add_variant(
        session,
        stable_entity=stable_entity,
        membership=membership,
        variant_type="verified_alias",
        surface_text=surface_text,
        language=language,
    )


def add_romanization_variant(
    session: Session,
    *,
    stable_entity: StableEntityORM,
    membership: StableEntityMembershipORM,
    surface_text: str,
    language: str = _ROMANIZATION_LANGUAGE,
) -> StableEntityVariantORM:
    """Record one deterministic Latin-script spelling for a stable entity."""
    return _add_variant(
        session,
        stable_entity=stable_entity,
        membership=membership,
        variant_type="romanization",
        surface_text=surface_text,
        language=language,
    )


def romanize_stable_entity(
    session: Session,
    *,
    stable_entity: StableEntityORM,
    membership: StableEntityMembershipORM,
    arabic_text: str,
    max_variants: int = _DEFAULT_ROMANIZATION_CANDIDATES,
) -> list[StableEntityVariantORM]:
    """Romanize ``arabic_text`` with ``ArabicAdapter.romanize()`` and store it.

    This never creates a ``TranslationArtifact``: romanization is a local,
    deterministic transliteration, not a call to a translation provider, and
    the two must stay distinguishable in the data model rather than only in
    comments.
    """
    candidates = ArabicAdapter().romanize(arabic_text)
    rows = []
    for candidate in candidates[:max_variants]:
        rows.append(
            add_romanization_variant(
                session,
                stable_entity=stable_entity,
                membership=membership,
                surface_text=candidate,
            )
        )
    return rows


def active_variants(
    session: Session,
    stable_entity_id: int,
    *,
    variant_type: str | None = None,
) -> list[StableEntityVariantORM]:
    """Variants anchored to the *currently active* resolver generation.

    A variant row is never deleted when its stable entity is later retracted
    (Global Constraint 7) -- it stays on file as history.  But it also must
    not read as "current" once the membership that witnesses it belongs to a
    generation the resolution-state pointer has moved past.  This filters
    strictly by ``StableEntityResolutionStateORM.active_generation_id``, so
    a retracted membership's variants drop out of the active view even
    though their rows remain intact for audit.
    """
    state = session.get(StableEntityResolutionStateORM, 1)
    if state is None or state.active_generation_id is None:
        return []

    stmt = (
        select(StableEntityVariantORM)
        .join(
            StableEntityMembershipORM,
            StableEntityVariantORM.source_membership_id == StableEntityMembershipORM.id,
        )
        .where(
            StableEntityVariantORM.stable_entity_id == stable_entity_id,
            StableEntityMembershipORM.generation_id == state.active_generation_id,
        )
    )
    if variant_type is not None:
        stmt = stmt.where(StableEntityVariantORM.variant_type == variant_type)
    return list(session.scalars(stmt).all())


def active_translation_uses(
    session: Session,
    stable_entity_id: int,
) -> list[TranslationUseORM]:
    """Translation uses anchored to a stable entity's currently active membership.

    Mirrors ``active_variants()``: a use whose ``source_membership_id``
    belongs to a generation the resolution-state pointer has moved past is
    excluded here, though the row itself is never deleted -- append-only
    history survives (Global Constraint 7).

    This is deliberately scoped to uses anchored to a stable entity via a
    membership.  A use with no membership at all (a pure evidence-span
    translation -- e.g. a document title -- never claimed to come from any
    stable entity) has no generation to retract from and is not part of
    this view; it is a permanent fact about an evidence span, not something
    that can go stale when a resolver generation advances.  See ADR 0031.
    """
    state = session.get(StableEntityResolutionStateORM, 1)
    if state is None or state.active_generation_id is None:
        return []

    stmt = (
        select(TranslationUseORM)
        .join(
            StableEntityMembershipORM,
            TranslationUseORM.source_membership_id == StableEntityMembershipORM.id,
        )
        .join(
            StableEntitySnapshotORM,
            StableEntitySnapshotORM.id == StableEntityMembershipORM.snapshot_id,
        )
        .where(
            StableEntitySnapshotORM.stable_entity_id == stable_entity_id,
            StableEntityMembershipORM.generation_id == state.active_generation_id,
        )
    )
    return list(session.scalars(stmt).all())
