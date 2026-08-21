"""Observe durable stable-entity generations without changing the M4 resolver.

``EntityORM`` is intentionally disposable: every legacy resolver recompute
retracts its rows and creates another generation.  This module builds a
separate, append-only projection from those rows and the M4.2a durable
evidence identities.  Calling :func:`observe_live_entity_generation` is an
explicit opt-in.  ``resolve_all`` shares the output lock so observation cannot
race its legacy rewrite, but never invokes the observer; the scheduled
resolver remains in legacy behavior until a later enforcement
checkpoint is deliberately approved.

The projection has one mutable singleton pointer only.  It is locked while a
complete generation is written, then moved to the newly complete generation
inside the caller's transaction.  Snapshots, memberships, and lineage are
otherwise append-only historical facts.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.store.identity import ConstraintRemap, remap_resolution_constraints
from src.store.orm import (
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

OBSERVE_MODE = "observe"
RECONCILER_VERSION = "1"

# One transaction-scoped PostgreSQL advisory lock shared by the legacy entity
# writer and this observer.  The stable-state row lock protects the pointer,
# but only this lock also prevents an observer from reading the legacy output
# while ``resolve_all`` is retracting and recreating it.
_RESOLUTION_OUTPUT_ADVISORY_LOCK_KEY = 4_203_201_042


class StableEntityInvariantError(ValueError):
    """A legacy cluster cannot be represented safely as stable history."""


@dataclass(frozen=True, slots=True)
class ObservedMembership:
    """One durable evidence endpoint selected from a legacy entity cluster."""

    evidence_identity_id: int
    evidence_fingerprint: str
    source_mention_id: int
    source_document_id: int


@dataclass(frozen=True, slots=True)
class ObservedEntityCluster:
    """The current legacy cluster as a deterministic evidence set."""

    source_entity_id: int
    object_type: str
    canonical_name: str
    memberships: tuple[ObservedMembership, ...]

    @property
    def evidence_identity_ids(self) -> frozenset[int]:
        return frozenset(item.evidence_identity_id for item in self.memberships)

    @property
    def evidence_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted(item.evidence_fingerprint for item in self.memberships))


@dataclass(frozen=True, slots=True)
class ObservedGeneration:
    """The durable result of one explicit observe-only capture."""

    generation_id: int
    generation_uid: str
    sequence: int
    parent_generation_uid: str | None
    stable_entities_created: int
    present_snapshots: int
    absent_snapshots: int
    lineage_counts: dict[str, int]
    constraint_status_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "generation_uid": self.generation_uid,
            "sequence": self.sequence,
            "mode": OBSERVE_MODE,
            "parent_generation_uid": self.parent_generation_uid,
            "stable_entities_created": self.stable_entities_created,
            "present_snapshots": self.present_snapshots,
            "absent_snapshots": self.absent_snapshots,
            "lineage_counts": dict(sorted(self.lineage_counts.items())),
            "constraint_status_counts": dict(sorted(self.constraint_status_counts.items())),
        }


@dataclass(frozen=True, slots=True)
class StableEntityMembershipView:
    evidence_fingerprint: str
    evidence_identity_id: int
    source_mention_id: int

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_fingerprint": self.evidence_fingerprint,
            "evidence_identity_id": self.evidence_identity_id,
            "source_mention_id": self.source_mention_id,
        }


@dataclass(frozen=True, slots=True)
class StableEntitySnapshotView:
    generation_uid: str
    generation_sequence: int
    canonical_name: str
    is_present: bool
    source_entity_id: int | None
    membership_digest: str
    memberships: tuple[StableEntityMembershipView, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_uid": self.generation_uid,
            "generation_sequence": self.generation_sequence,
            "canonical_name": self.canonical_name,
            "is_present": self.is_present,
            "source_entity_id": self.source_entity_id,
            "membership_digest": self.membership_digest,
            "memberships": [item.as_dict() for item in self.memberships],
        }


@dataclass(frozen=True, slots=True)
class StableEntitySnapshotSummary:
    generation_uid: str
    generation_sequence: int
    canonical_name: str
    is_present: bool
    source_entity_id: int | None
    membership_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_uid": self.generation_uid,
            "generation_sequence": self.generation_sequence,
            "canonical_name": self.canonical_name,
            "is_present": self.is_present,
            "source_entity_id": self.source_entity_id,
            "membership_digest": self.membership_digest,
        }


@dataclass(frozen=True, slots=True)
class StableEntityLineageView:
    generation_uid: str
    generation_sequence: int
    relationship: str
    from_stable_uid: str
    to_stable_uid: str
    witnesses: tuple["StableEntityLineageWitnessView", ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_uid": self.generation_uid,
            "generation_sequence": self.generation_sequence,
            "relationship": self.relationship,
            "from_stable_uid": self.from_stable_uid,
            "to_stable_uid": self.to_stable_uid,
            "witnesses": [witness.as_dict() for witness in self.witnesses],
        }


@dataclass(frozen=True, slots=True)
class StableEntityLineageWitnessView:
    """Durable evidence and source anchor that justifies one lineage edge."""

    evidence_fingerprint: str
    evidence_identity_id: int
    source_mention_id: int

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_fingerprint": self.evidence_fingerprint,
            "evidence_identity_id": self.evidence_identity_id,
            "source_mention_id": self.source_mention_id,
        }


@dataclass(frozen=True, slots=True)
class StableEntityHistory:
    """Read-only history contract for one durable entity URL/identifier."""

    stable_uid: str
    object_type: str
    active_generation_uid: str | None
    as_of_generation_uid: str | None
    as_of_generation_sequence: int | None
    as_of_snapshot: StableEntitySnapshotView | None
    current_target_uids: tuple[str, ...]
    snapshots: tuple[StableEntitySnapshotSummary, ...]
    lineage: tuple[StableEntityLineageView, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "stable_uid": self.stable_uid,
            "object_type": self.object_type,
            "active_generation_uid": self.active_generation_uid,
            "as_of_generation_uid": self.as_of_generation_uid,
            "as_of_generation_sequence": self.as_of_generation_sequence,
            "as_of_snapshot": (
                self.as_of_snapshot.as_dict() if self.as_of_snapshot is not None else None
            ),
            "current_target_uids": list(self.current_target_uids),
            "snapshots": [item.as_dict() for item in self.snapshots],
            "lineage": [item.as_dict() for item in self.lineage],
        }


@dataclass(frozen=True, slots=True)
class _PriorEntity:
    stable_entity_id: int
    stable_uid: str
    object_type: str
    evidence_identity_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class _ParentState:
    generation: ResolverGenerationORM | None
    snapshots_by_entity_id: dict[int, StableEntitySnapshotORM]
    present_entities: dict[int, _PriorEntity]


@dataclass(frozen=True, slots=True)
class _ReconciliationPlan:
    assignments: dict[int, int | None]
    overlaps: dict[int, dict[int, int]]
    primary_child_by_prior: dict[int, int]


def observe_live_entity_generation(
    session: Session,
    *,
    resolver_extractor_version_id: int,
    reconciler_version: str = RECONCILER_VERSION,
    mode: str = OBSERVE_MODE,
) -> ObservedGeneration:
    """Capture current ``EntityORM`` rows as one active observe-only generation.

    This does not change or consume the legacy resolver's clusters, scores,
    constraints, public data, or release state.  It simply projects those
    already-written live clusters into durable stable history.  The call is
    intentionally explicit, which is the safety gate for M4.2b: code that
    only calls ``resolve_all`` remains in legacy mode.

    The caller owns the enclosing transaction.  Pending legacy writes are
    flushed before capture, then the immutable projection is written in its
    own savepoint.  A validation or late write failure therefore rolls back
    both the new immutable rows and the active-generation pointer even if the
    caller catches the exception and continues its outer transaction.
    """

    if mode != OBSERVE_MODE:
        raise StableEntityInvariantError(
            "M4.2b supports only explicit observe mode; enforcement is deferred"
        )
    if not reconciler_version.strip():
        raise StableEntityInvariantError("reconciler_version must not be empty")

    # ``autoflush=False`` is common for batch callers.  Take the shared lock
    # *before* the explicit flush: PostgreSQL would otherwise write a
    # caller's pending legacy entity retraction/rebuild outside the lock that
    # protects resolver output from concurrent observation.
    with session.no_autoflush:
        acquire_resolution_output_lock(session)
    session.flush()
    with session.begin_nested():
        return _capture_live_entity_generation(
            session,
            resolver_extractor_version_id=resolver_extractor_version_id,
            reconciler_version=reconciler_version.strip(),
        )


def acquire_resolution_output_lock(session: Session) -> None:
    """Serialize legacy entity writes and explicit stable observation.

    PostgreSQL receives a transaction-scoped advisory lock before either path
    reads or mutates the disposable entity output.  SQLite has no compatible
    advisory primitive; its unit-test transactions are intentionally single
    writer, so this remains a no-op there.
    """

    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _RESOLUTION_OUTPUT_ADVISORY_LOCK_KEY},
        )


def _capture_live_entity_generation(
    session: Session,
    *,
    resolver_extractor_version_id: int,
    reconciler_version: str,
) -> ObservedGeneration:
    resolver = session.get(ExtractorVersionORM, resolver_extractor_version_id)
    if resolver is None:
        raise StableEntityInvariantError(
            f"resolver extractor version {resolver_extractor_version_id} does not exist"
        )

    # A PostgreSQL row lock serializes sequence allocation, reconciliation
    # against the active parent, and the final pointer switch.  SQLite accepts
    # ``FOR UPDATE`` as a no-op in tests, where one session owns the in-memory
    # database.
    state = _locked_resolution_state(session)
    clusters = _load_live_entity_clusters(session)
    _validate_live_entity_provenance(
        session,
        clusters=clusters,
        resolver_extractor_version_id=resolver.id,
    )
    remaps = remap_resolution_constraints(session)
    constraint_status_counts = _constraint_status_counts(remaps)
    input_digest = _generation_input_digest(clusters, remaps, reconciler_version)

    known_entities = {
        row.id: row
        for row in session.scalars(select(StableEntityORM).order_by(StableEntityORM.id))
    }
    parent = _load_parent_state(session, state, known_entities)
    plan = _reconcile_clusters(clusters, parent.present_entities)

    sequence = int(state.max_generation_sequence) + 1
    generation = ResolverGenerationORM(
        generation_uid=str(uuid.uuid4()),
        sequence=sequence,
        mode=OBSERVE_MODE,
        parent_generation_id=parent.generation.id if parent.generation else None,
        resolver_extractor_version_id=resolver.id,
        reconciler_version=reconciler_version,
        input_digest=input_digest,
        constraint_status_counts=constraint_status_counts,
    )
    session.add(generation)
    session.flush()

    new_stable_entities = _create_new_stable_entities(session, clusters, plan)
    known_entities.update(new_stable_entities)
    _assert_assignments_are_complete(plan.assignments, clusters, known_entities)

    snapshots_by_stable_id, clusters_by_stable_id = _write_full_snapshots(
        session,
        generation=generation,
        clusters=clusters,
        assignments=plan.assignments,
        known_entities=known_entities,
        parent=parent,
    )
    memberships_by_stable_evidence = _write_memberships_and_provenance(
        session,
        snapshots_by_stable_id=snapshots_by_stable_id,
        clusters_by_stable_id=clusters_by_stable_id,
        resolver_extractor_version_id=resolver.id,
    )
    lineage_counts = _write_lineage(
        session,
        generation_id=generation.id,
        plan=plan,
        assignments=plan.assignments,
        clusters=clusters,
        parent=parent,
        memberships_by_stable_evidence=memberships_by_stable_evidence,
    )

    # Move the only mutable pointer last.  If any earlier step raises, the
    # nested transaction leaves the prior observed generation active.
    state.active_generation_id = generation.id
    state.max_generation_sequence = sequence
    state.updated_at = _utcnow()
    session.flush()

    return ObservedGeneration(
        generation_id=generation.id,
        generation_uid=generation.generation_uid,
        sequence=generation.sequence,
        parent_generation_uid=(
            parent.generation.generation_uid if parent.generation is not None else None
        ),
        stable_entities_created=len(new_stable_entities),
        present_snapshots=len(clusters_by_stable_id),
        absent_snapshots=len(known_entities) - len(clusters_by_stable_id),
        lineage_counts=lineage_counts,
        constraint_status_counts=constraint_status_counts,
    )


def stable_entity_history(
    session: Session,
    stable_uid: str,
    *,
    as_of_sequence: int | None = None,
) -> StableEntityHistory | None:
    """Return a read-only as-of membership, canonical-name, and lineage view.

    ``as_of_sequence`` names one immutable observed generation.  Omit it to
    read the generation currently selected by the mutable coordination
    pointer.  This function deliberately performs selects only; the standalone
    history script uses it without calling the resolver or any adoption path.
    """

    stable = session.scalar(
        select(StableEntityORM).where(StableEntityORM.stable_uid == stable_uid)
    )
    if stable is None:
        return None

    state = session.get(StableEntityResolutionStateORM, 1)
    active_generation = (
        session.get(ResolverGenerationORM, state.active_generation_id)
        if state is not None and state.active_generation_id is not None
        else None
    )
    target_generation = _history_generation(
        session,
        active_generation=active_generation,
        as_of_sequence=as_of_sequence,
    )
    if target_generation is None:
        return StableEntityHistory(
            stable_uid=stable.stable_uid,
            object_type=stable.object_type,
            active_generation_uid=(
                active_generation.generation_uid if active_generation is not None else None
            ),
            as_of_generation_uid=None,
            as_of_generation_sequence=None,
            as_of_snapshot=None,
            current_target_uids=(),
            snapshots=(),
            lineage=(),
        )

    snapshot = session.scalar(
        select(StableEntitySnapshotORM).where(
            StableEntitySnapshotORM.generation_id == target_generation.id,
            StableEntitySnapshotORM.stable_entity_id == stable.id,
        )
    )
    snapshot_view = (
        _snapshot_view(session, snapshot, target_generation)
        if snapshot is not None
        else None
    )
    target_uids = _current_target_uids(
        session,
        stable_entity_id=stable.id,
        target_generation=target_generation,
    )

    return StableEntityHistory(
        stable_uid=stable.stable_uid,
        object_type=stable.object_type,
        active_generation_uid=(
            active_generation.generation_uid if active_generation is not None else None
        ),
        as_of_generation_uid=target_generation.generation_uid,
        as_of_generation_sequence=target_generation.sequence,
        as_of_snapshot=snapshot_view,
        current_target_uids=target_uids,
        snapshots=_snapshot_summaries(session, stable.id, target_generation.sequence),
        lineage=_lineage_views(session, stable.id, target_generation.sequence),
    )


def stable_entity_snapshot_as_of(
    session: Session,
    stable_uid: str,
    *,
    as_of_sequence: int | None = None,
) -> StableEntitySnapshotView | None:
    """Return only the exact stable-entity state for one observed generation."""

    history = stable_entity_history(
        session,
        stable_uid,
        as_of_sequence=as_of_sequence,
    )
    return history.as_of_snapshot if history is not None else None


def _locked_resolution_state(session: Session) -> StableEntityResolutionStateORM:
    row = session.scalar(
        select(StableEntityResolutionStateORM)
        .where(StableEntityResolutionStateORM.id == 1)
        .with_for_update()
    )
    if row is not None:
        return row

    # ``create_all`` unit fixtures do not run the migration's singleton
    # insert.  Production receives this row from 0005; the fallback preserves
    # the same invariant for an isolated test database.
    row = StableEntityResolutionStateORM(
        id=1,
        active_generation_id=None,
        max_generation_sequence=0,
        updated_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def _load_live_entity_clusters(session: Session) -> list[ObservedEntityCluster]:
    """Read current disposable clusters and collapse replacement mentions.

    The durable membership is keyed by an M4.2a evidence identity rather than
    a raw mention ID.  If two currently live raw mentions map to the same
    evidence fingerprint, choose the smallest mention ID as the provenance
    anchor; the evidence is included once, not accidentally counted twice.
    """

    rows = session.execute(
        select(
            EntityORM.id,
            EntityORM.object_type,
            EntityORM.canonical_name,
            EntityMentionORM.mention_id,
            MentionORM.document_id,
            MentionORM.retracted,
            DocumentORM.retracted,
            MentionEvidenceIdentityORM.evidence_identity_id,
            EvidenceIdentityORM.fingerprint,
            EvidenceIdentityORM.object_type,
        )
        .select_from(EntityORM)
        .outerjoin(EntityMentionORM, EntityMentionORM.entity_id == EntityORM.id)
        .outerjoin(MentionORM, MentionORM.id == EntityMentionORM.mention_id)
        .outerjoin(DocumentORM, DocumentORM.id == MentionORM.document_id)
        .outerjoin(
            MentionEvidenceIdentityORM,
            MentionEvidenceIdentityORM.mention_id == MentionORM.id,
        )
        .outerjoin(
            EvidenceIdentityORM,
            EvidenceIdentityORM.id == MentionEvidenceIdentityORM.evidence_identity_id,
        )
        .where(EntityORM.retracted.is_(False))
        .order_by(EntityORM.id, EntityMentionORM.mention_id)
    ).all()

    grouped: dict[int, dict[str, object]] = {}
    for (
        entity_id,
        object_type,
        canonical_name,
        mention_id,
        document_id,
        mention_retracted,
        document_retracted,
        evidence_identity_id,
        fingerprint,
        evidence_object_type,
    ) in rows:
        current = grouped.setdefault(
            entity_id,
            {
                "object_type": object_type,
                "canonical_name": canonical_name,
                "memberships": {},
            },
        )
        if mention_id is None:
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} has no evidence mentions"
            )
        if mention_retracted is None or document_retracted is None or document_id is None:
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} has missing evidence mention or document "
                f"for entity mention {mention_id}; rerun the legacy resolver before observation"
            )
        if bool(mention_retracted) or bool(document_retracted):
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} uses retracted evidence mention "
                f"{mention_id}; rerun the legacy resolver before observation"
            )
        if (
            evidence_identity_id is None
            or fingerprint is None
            or evidence_object_type is None
        ):
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} uses mention {mention_id} without durable evidence; "
                "run the M4.2a identity adoption first"
            )
        if evidence_object_type != object_type:
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} has type {object_type!r} but evidence "
                f"{evidence_identity_id} has type {evidence_object_type!r}"
            )

        memberships = current["memberships"]
        assert isinstance(memberships, dict)
        existing = memberships.get(evidence_identity_id)
        candidate = ObservedMembership(
            evidence_identity_id=evidence_identity_id,
            evidence_fingerprint=fingerprint,
            source_mention_id=mention_id,
            source_document_id=document_id,
        )
        if existing is None or candidate.source_mention_id < existing.source_mention_id:
            memberships[evidence_identity_id] = candidate

    clusters: list[ObservedEntityCluster] = []
    evidence_owner: dict[int, int] = {}
    for entity_id in sorted(grouped):
        current = grouped[entity_id]
        memberships = current["memberships"]
        assert isinstance(memberships, dict)
        values = tuple(sorted(memberships.values(), key=lambda item: item.evidence_fingerprint))
        if not values:
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} has no durable evidence identities"
            )
        for membership in values:
            prior_owner = evidence_owner.setdefault(membership.evidence_identity_id, entity_id)
            if prior_owner != entity_id:
                raise StableEntityInvariantError(
                    "one durable evidence identity belongs to two live legacy entities: "
                    f"{membership.evidence_identity_id} in {prior_owner} and {entity_id}"
                )
        object_type = current["object_type"]
        canonical_name = current["canonical_name"]
        assert isinstance(object_type, str)
        assert isinstance(canonical_name, str)
        clusters.append(
            ObservedEntityCluster(
                source_entity_id=entity_id,
                object_type=object_type,
                canonical_name=canonical_name,
                memberships=values,
            )
        )
    return clusters


def _validate_live_entity_provenance(
    session: Session,
    *,
    clusters: Iterable[ObservedEntityCluster],
    resolver_extractor_version_id: int,
) -> None:
    """Prove live source entities were written by the claimed resolver.

    ``EntityORM`` is disposable, so a caller-provided extractor version is
    only meaningful when every current entity membership has exactly one
    matching legacy provenance row.  This rejects a mixed/manual entity or a
    missing provenance row before it can become durable stable history.
    """

    entity_ids = sorted(cluster.source_entity_id for cluster in clusters)
    if not entity_ids:
        return

    expected_by_entity: dict[int, dict[int, int]] = defaultdict(dict)
    member_rows = session.execute(
        select(
            EntityMentionORM.entity_id,
            EntityMentionORM.mention_id,
            MentionORM.document_id,
        )
        .join(MentionORM, MentionORM.id == EntityMentionORM.mention_id)
        .where(EntityMentionORM.entity_id.in_(entity_ids))
        .order_by(EntityMentionORM.entity_id, EntityMentionORM.mention_id)
    ).all()
    for entity_id, mention_id, document_id in member_rows:
        if document_id is None:
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} has missing source document for "
                f"mention {mention_id}; rerun the legacy resolver before observation"
            )
        expected_by_entity[entity_id][mention_id] = document_id

    provenance_by_entity: dict[int, list[tuple[int | None, int, int]]] = defaultdict(list)
    provenance_rows = session.execute(
        select(
            ProvenanceORM.target_id,
            ProvenanceORM.mention_id,
            ProvenanceORM.document_id,
            ProvenanceORM.extractor_version_id,
        )
        .where(
            ProvenanceORM.target_table == "entities",
            ProvenanceORM.target_id.in_(entity_ids),
        )
        .order_by(ProvenanceORM.target_id, ProvenanceORM.id)
    ).all()
    for entity_id, mention_id, document_id, extractor_version_id in provenance_rows:
        provenance_by_entity[entity_id].append(
            (mention_id, document_id, extractor_version_id)
        )

    for entity_id in entity_ids:
        expected = expected_by_entity.get(entity_id, {})
        actual = provenance_by_entity.get(entity_id, [])
        by_mention: dict[int | None, list[tuple[int, int]]] = defaultdict(list)
        for mention_id, document_id, extractor_version_id in actual:
            by_mention[mention_id].append((document_id, extractor_version_id))

        if set(by_mention) != set(expected):
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} lacks one resolver provenance row per "
                "evidence mention; rerun the legacy resolver before observation"
            )
        for mention_id, document_id in expected.items():
            rows = by_mention[mention_id]
            if len(rows) != 1 or rows[0][0] != document_id:
                raise StableEntityInvariantError(
                    f"live legacy entity {entity_id} has invalid resolver provenance for "
                    f"evidence mention {mention_id}; rerun the legacy resolver before observation"
                )
        versions = {extractor_version_id for _mention_id, _document_id, extractor_version_id in actual}
        if versions != {resolver_extractor_version_id}:
            raise StableEntityInvariantError(
                f"live legacy entity {entity_id} provenance extractor versions "
                f"{sorted(versions)} do not match claimed resolver "
                f"{resolver_extractor_version_id}"
            )


def _load_parent_state(
    session: Session,
    state: StableEntityResolutionStateORM,
    known_entities: dict[int, StableEntityORM],
) -> _ParentState:
    if state.active_generation_id is None:
        if known_entities:
            raise StableEntityInvariantError(
                "stable entities exist without an active observed generation"
            )
        return _ParentState(None, {}, {})

    generation = session.get(ResolverGenerationORM, state.active_generation_id)
    if generation is None:
        raise StableEntityInvariantError(
            f"active observed generation {state.active_generation_id} is missing"
        )
    snapshots = list(
        session.scalars(
            select(StableEntitySnapshotORM)
            .where(StableEntitySnapshotORM.generation_id == generation.id)
            .order_by(StableEntitySnapshotORM.stable_entity_id)
        )
    )
    snapshots_by_entity_id = {row.stable_entity_id: row for row in snapshots}
    if set(snapshots_by_entity_id) != set(known_entities):
        raise StableEntityInvariantError(
            "active observed generation does not contain one snapshot for every stable entity"
        )

    memberships_by_snapshot: dict[int, list[tuple[int, str]]] = defaultdict(list)
    if snapshots:
        snapshots_by_id = {snapshot.id: snapshot for snapshot in snapshots}
        evidence_owner: dict[int, int] = {}
        rows = session.execute(
            select(
                StableEntityMembershipORM.snapshot_id,
                StableEntityMembershipORM.generation_id,
                StableEntityMembershipORM.evidence_identity_id,
                EvidenceIdentityORM.fingerprint,
            )
            .join(
                EvidenceIdentityORM,
                EvidenceIdentityORM.id == StableEntityMembershipORM.evidence_identity_id,
            )
            .where(
                StableEntityMembershipORM.snapshot_id.in_([row.id for row in snapshots])
            )
            .order_by(
                StableEntityMembershipORM.snapshot_id,
                EvidenceIdentityORM.fingerprint,
            )
        ).all()
        for snapshot_id, membership_generation_id, evidence_id, fingerprint in rows:
            if membership_generation_id != generation.id:
                raise StableEntityInvariantError(
                    f"stable membership for snapshot {snapshot_id} names generation "
                    f"{membership_generation_id}, not active generation {generation.id}"
                )
            prior_snapshot_id = evidence_owner.setdefault(evidence_id, snapshot_id)
            if prior_snapshot_id != snapshot_id:
                raise StableEntityInvariantError(
                    "one durable evidence identity belongs to two active stable snapshots: "
                    f"{evidence_id} in {prior_snapshot_id} and {snapshot_id}"
                )
            if snapshot_id not in snapshots_by_id:
                raise StableEntityInvariantError(
                    f"stable membership names snapshot {snapshot_id} outside active generation"
                )
            memberships_by_snapshot[snapshot_id].append((evidence_id, fingerprint))

    present_entities: dict[int, _PriorEntity] = {}
    for stable_id, snapshot in snapshots_by_entity_id.items():
        members = memberships_by_snapshot[snapshot.id]
        expected_digest = _membership_digest(fingerprint for _id, fingerprint in members)
        if snapshot.membership_digest != expected_digest:
            raise StableEntityInvariantError(
                f"stable snapshot {snapshot.id} has a membership digest mismatch"
            )
        if snapshot.is_present:
            if not members:
                raise StableEntityInvariantError(
                    f"present stable snapshot {snapshot.id} has no durable evidence"
                )
            stable = known_entities[stable_id]
            present_entities[stable_id] = _PriorEntity(
                stable_entity_id=stable_id,
                stable_uid=stable.stable_uid,
                object_type=stable.object_type,
                evidence_identity_ids=frozenset(evidence_id for evidence_id, _ in members),
            )
        elif members:
            raise StableEntityInvariantError(
                f"absent stable snapshot {snapshot.id} has membership rows"
            )
    return _ParentState(generation, snapshots_by_entity_id, present_entities)


def _reconcile_clusters(
    clusters: list[ObservedEntityCluster],
    prior_entities: dict[int, _PriorEntity],
) -> _ReconciliationPlan:
    """Choose durable continuations using only exact durable evidence overlap.

    For a simple split, each prior stable entity nominates its largest child;
    the stable UID stays with that child.  For a merge, all predecessors
    nominate the same child and the highest-overlap predecessor wins, with a
    stable-UID lexical tie-break.  Other children/new clusters receive fresh
    UIDs.  This is deliberately deterministic and never compares text.
    """

    overlaps: dict[int, dict[int, int]] = {}
    for stable_id, prior in prior_entities.items():
        by_cluster: dict[int, int] = {}
        for index, cluster in enumerate(clusters):
            overlap = len(prior.evidence_identity_ids.intersection(cluster.evidence_identity_ids))
            if not overlap:
                continue
            if prior.object_type != cluster.object_type:
                raise StableEntityInvariantError(
                    f"durable evidence overlaps stable entity {prior.stable_uid} across "
                    f"object types {prior.object_type!r} and {cluster.object_type!r}"
                )
            by_cluster[index] = overlap
        if by_cluster:
            overlaps[stable_id] = by_cluster

    primary_child_by_prior: dict[int, int] = {}
    predecessor_candidates: dict[int, list[int]] = defaultdict(list)
    for stable_id, by_cluster in overlaps.items():
        primary = min(
            by_cluster,
            key=lambda index: (
                -by_cluster[index],
                _cluster_tie_breaker(clusters[index]),
            ),
        )
        primary_child_by_prior[stable_id] = primary
        predecessor_candidates[primary].append(stable_id)

    assignments: dict[int, int | None] = {index: None for index in range(len(clusters))}
    for index, candidates in predecessor_candidates.items():
        assignments[index] = min(
            candidates,
            key=lambda stable_id: (
                -overlaps[stable_id][index],
                prior_entities[stable_id].stable_uid,
            ),
        )
    return _ReconciliationPlan(assignments, overlaps, primary_child_by_prior)


def _create_new_stable_entities(
    session: Session,
    clusters: list[ObservedEntityCluster],
    plan: _ReconciliationPlan,
) -> dict[int, StableEntityORM]:
    created_for_index: dict[int, StableEntityORM] = {}
    for index, stable_id in plan.assignments.items():
        if stable_id is not None:
            continue
        cluster = clusters[index]
        row = StableEntityORM(
            stable_uid=str(uuid.uuid4()),
            object_type=cluster.object_type,
        )
        session.add(row)
        created_for_index[index] = row
    session.flush()

    by_id: dict[int, StableEntityORM] = {}
    for index, row in created_for_index.items():
        if row.id is None:
            raise StableEntityInvariantError("new stable entity did not receive an id")
        plan.assignments[index] = row.id
        by_id[row.id] = row
    return by_id


def _assert_assignments_are_complete(
    assignments: dict[int, int | None],
    clusters: list[ObservedEntityCluster],
    known_entities: dict[int, StableEntityORM],
) -> None:
    assigned_stable_ids: set[int] = set()
    for index, stable_id in assignments.items():
        if stable_id is None or stable_id not in known_entities:
            raise StableEntityInvariantError(f"cluster {index} has no stable entity assignment")
        if stable_id in assigned_stable_ids:
            raise StableEntityInvariantError(
                f"stable entity {stable_id} was assigned to two current clusters"
            )
        assigned_stable_ids.add(stable_id)
        cluster = clusters[index]
        if known_entities[stable_id].object_type != cluster.object_type:
            raise StableEntityInvariantError(
                f"stable entity {stable_id} type does not match current cluster {index}"
            )


def _write_full_snapshots(
    session: Session,
    *,
    generation: ResolverGenerationORM,
    clusters: list[ObservedEntityCluster],
    assignments: dict[int, int | None],
    known_entities: dict[int, StableEntityORM],
    parent: _ParentState,
) -> tuple[dict[int, StableEntitySnapshotORM], dict[int, ObservedEntityCluster]]:
    clusters_by_stable_id: dict[int, ObservedEntityCluster] = {}
    for index, stable_id in assignments.items():
        assert stable_id is not None
        clusters_by_stable_id[stable_id] = clusters[index]

    snapshots_by_stable_id: dict[int, StableEntitySnapshotORM] = {}
    for stable_id in sorted(known_entities):
        cluster = clusters_by_stable_id.get(stable_id)
        if cluster is not None:
            snapshot = StableEntitySnapshotORM(
                generation_id=generation.id,
                stable_entity_id=stable_id,
                source_entity_id=cluster.source_entity_id,
                canonical_name=cluster.canonical_name,
                is_present=True,
                membership_digest=_membership_digest(cluster.evidence_fingerprints),
            )
        else:
            prior_snapshot = parent.snapshots_by_entity_id.get(stable_id)
            if prior_snapshot is None:
                raise StableEntityInvariantError(
                    f"stable entity {stable_id} is absent before it has a prior snapshot"
                )
            snapshot = StableEntitySnapshotORM(
                generation_id=generation.id,
                stable_entity_id=stable_id,
                source_entity_id=None,
                canonical_name=prior_snapshot.canonical_name,
                is_present=False,
                membership_digest=_membership_digest(()),
            )
        session.add(snapshot)
        snapshots_by_stable_id[stable_id] = snapshot
    session.flush()
    return snapshots_by_stable_id, clusters_by_stable_id


def _write_memberships_and_provenance(
    session: Session,
    *,
    snapshots_by_stable_id: dict[int, StableEntitySnapshotORM],
    clusters_by_stable_id: dict[int, ObservedEntityCluster],
    resolver_extractor_version_id: int,
) -> dict[tuple[int, int], StableEntityMembershipORM]:
    pending: list[tuple[StableEntityMembershipORM, ObservedMembership]] = []
    memberships_by_stable_evidence: dict[
        tuple[int, int], StableEntityMembershipORM
    ] = {}
    for stable_id, cluster in clusters_by_stable_id.items():
        snapshot = snapshots_by_stable_id[stable_id]
        for membership in cluster.memberships:
            row = StableEntityMembershipORM(
                snapshot_id=snapshot.id,
                generation_id=snapshot.generation_id,
                evidence_identity_id=membership.evidence_identity_id,
                source_mention_id=membership.source_mention_id,
            )
            session.add(row)
            pending.append((row, membership))
            memberships_by_stable_evidence[(stable_id, membership.evidence_identity_id)] = row
    session.flush()

    for row, membership in pending:
        session.add(
            ProvenanceORM(
                target_table="stable_entity_memberships",
                target_id=row.id,
                document_id=membership.source_document_id,
                mention_id=membership.source_mention_id,
                extractor_version_id=resolver_extractor_version_id,
            )
        )
    return memberships_by_stable_evidence


def _write_lineage(
    session: Session,
    *,
    generation_id: int,
    plan: _ReconciliationPlan,
    assignments: dict[int, int | None],
    clusters: list[ObservedEntityCluster],
    parent: _ParentState,
    memberships_by_stable_evidence: dict[tuple[int, int], StableEntityMembershipORM],
) -> dict[str, int]:
    edges: dict[tuple[int, int, str], frozenset[int]] = {}
    for stable_id, primary_index in plan.primary_child_by_prior.items():
        primary_target = assignments[primary_index]
        if primary_target is None:
            raise StableEntityInvariantError("primary continuation was not assigned")
        if primary_target == stable_id:
            relationship = "continued"
        else:
            relationship = "merged_into"
        _add_lineage_edge(
            edges,
            from_stable_id=stable_id,
            to_stable_id=primary_target,
            relationship=relationship,
            evidence_identity_ids=_lineage_overlap_witnesses(
                parent,
                stable_id=stable_id,
                cluster=clusters[primary_index],
            ),
        )

        for child_index in sorted(plan.overlaps[stable_id]):
            if child_index == primary_index:
                continue
            child_target = assignments[child_index]
            if child_target is None:
                raise StableEntityInvariantError("split child was not assigned")
            if child_target != stable_id:
                _add_lineage_edge(
                    edges,
                    from_stable_id=stable_id,
                    to_stable_id=child_target,
                    relationship="split_into",
                    evidence_identity_ids=_lineage_overlap_witnesses(
                        parent,
                        stable_id=stable_id,
                        cluster=clusters[child_index],
                    ),
                )

    counts: Counter[str] = Counter()
    rows_by_edge: dict[tuple[int, int, str], StableEntityLineageORM] = {}
    for edge in sorted(edges):
        from_stable_id, to_stable_id, relationship = edge
        row = StableEntityLineageORM(
            generation_id=generation_id,
            from_stable_entity_id=from_stable_id,
            to_stable_entity_id=to_stable_id,
            relationship=relationship,
        )
        session.add(row)
        rows_by_edge[edge] = row
        counts[relationship] += 1
    session.flush()

    for edge, evidence_identity_ids in edges.items():
        _from_stable_id, to_stable_id, _relationship = edge
        lineage = rows_by_edge[edge]
        for evidence_identity_id in sorted(evidence_identity_ids):
            membership = memberships_by_stable_evidence.get(
                (to_stable_id, evidence_identity_id)
            )
            if membership is None:
                raise StableEntityInvariantError(
                    f"lineage edge {lineage.id} has no current membership witness for "
                    f"durable evidence {evidence_identity_id}"
                )
            session.add(
                StableEntityLineageEvidenceORM(
                    lineage_id=lineage.id,
                    evidence_identity_id=evidence_identity_id,
                    source_membership_id=membership.id,
                )
            )
    return dict(sorted(counts.items()))


def _add_lineage_edge(
    edges: dict[tuple[int, int, str], frozenset[int]],
    *,
    from_stable_id: int,
    to_stable_id: int,
    relationship: str,
    evidence_identity_ids: frozenset[int],
) -> None:
    if not evidence_identity_ids:
        raise StableEntityInvariantError(
            f"{relationship} lineage from stable entity {from_stable_id} has no "
            "durable evidence witness"
        )
    edge = (from_stable_id, to_stable_id, relationship)
    existing = edges.get(edge)
    if existing is not None and existing != evidence_identity_ids:
        raise StableEntityInvariantError(
            f"duplicate lineage edge {edge} has inconsistent durable evidence witnesses"
        )
    edges[edge] = evidence_identity_ids


def _lineage_overlap_witnesses(
    parent: _ParentState,
    *,
    stable_id: int,
    cluster: ObservedEntityCluster,
) -> frozenset[int]:
    prior = parent.present_entities.get(stable_id)
    if prior is None:
        raise StableEntityInvariantError(
            f"lineage source stable entity {stable_id} was not present in the parent "
            "generation"
        )
    return prior.evidence_identity_ids.intersection(cluster.evidence_identity_ids)


def _generation_input_digest(
    clusters: Iterable[ObservedEntityCluster],
    remaps: Iterable[ConstraintRemap],
    reconciler_version: str,
) -> str:
    """Hash durable, source-independent inputs without disposable row IDs."""

    cluster_items = [
        {
            "object_type": cluster.object_type,
            "canonical_name": cluster.canonical_name,
            "evidence_fingerprints": list(cluster.evidence_fingerprints),
        }
        for cluster in clusters
    ]
    cluster_items.sort(
        key=lambda item: (
            item["object_type"],
            item["canonical_name"],
            tuple(item["evidence_fingerprints"]),
        )
    )
    remap_rows = tuple(remaps)
    signatures_by_id = {
        remap.constraint_id: _durable_constraint_signature(remap) for remap in remap_rows
    }
    constraint_items = []
    for remap in remap_rows:
        item = {
            **_durable_constraint_signature(remap),
            "status": remap.status,
            "reason": remap.reason,
            "conflicting_constraint_signatures": sorted(
                (
                    signatures_by_id[constraint_id]
                    for constraint_id in remap.conflicting_constraint_ids
                    if constraint_id in signatures_by_id
                ),
                key=_canonical_json_key,
            ),
        }
        if len(item["conflicting_constraint_signatures"]) != len(
            remap.conflicting_constraint_ids
        ):
            raise StableEntityInvariantError(
                "constraint remap refers to a conflict that is absent from the current "
                "durable remap projection"
            )
        constraint_items.append(item)
    constraint_items.sort(key=_canonical_json_key)
    return _canonical_digest(
        {
            "reconciler_version": reconciler_version,
            "clusters": cluster_items,
            "constraints": constraint_items,
        }
    )


def _constraint_status_counts(remaps: Iterable[ConstraintRemap]) -> dict[str, int]:
    counts = Counter(remap.status for remap in remaps)
    return dict(sorted(counts.items()))


def _cluster_tie_breaker(cluster: ObservedEntityCluster) -> tuple[object, ...]:
    return (
        cluster.object_type,
        cluster.evidence_fingerprints,
    )


def _durable_constraint_signature(remap: ConstraintRemap) -> dict[str, object]:
    """Return the endpoint/decision identity with no surrogate row IDs."""

    return {
        "decision": remap.decision,
        "evidence_fingerprints": sorted(
            (remap.left_evidence_fingerprint, remap.right_evidence_fingerprint)
        ),
    }


def _canonical_json_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _membership_digest(fingerprints: Iterable[str]) -> str:
    return _canonical_digest({"evidence_fingerprints": sorted(fingerprints)})


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _history_generation(
    session: Session,
    *,
    active_generation: ResolverGenerationORM | None,
    as_of_sequence: int | None,
) -> ResolverGenerationORM | None:
    if as_of_sequence is None:
        return active_generation
    if as_of_sequence <= 0:
        raise ValueError("as_of_sequence must be positive")
    generation = session.scalar(
        select(ResolverGenerationORM).where(ResolverGenerationORM.sequence == as_of_sequence)
    )
    if generation is None:
        raise ValueError(f"observed generation sequence {as_of_sequence} does not exist")
    return generation


def _snapshot_view(
    session: Session,
    snapshot: StableEntitySnapshotORM,
    generation: ResolverGenerationORM,
) -> StableEntitySnapshotView:
    rows = session.execute(
        select(
            StableEntityMembershipORM.evidence_identity_id,
            EvidenceIdentityORM.fingerprint,
            StableEntityMembershipORM.source_mention_id,
        )
        .join(
            EvidenceIdentityORM,
            EvidenceIdentityORM.id == StableEntityMembershipORM.evidence_identity_id,
        )
        .where(StableEntityMembershipORM.snapshot_id == snapshot.id)
        .order_by(EvidenceIdentityORM.fingerprint, StableEntityMembershipORM.id)
    ).all()
    memberships = tuple(
        StableEntityMembershipView(
            evidence_identity_id=evidence_identity_id,
            evidence_fingerprint=fingerprint,
            source_mention_id=source_mention_id,
        )
        for evidence_identity_id, fingerprint, source_mention_id in rows
    )
    return StableEntitySnapshotView(
        generation_uid=generation.generation_uid,
        generation_sequence=generation.sequence,
        canonical_name=snapshot.canonical_name,
        is_present=snapshot.is_present,
        source_entity_id=snapshot.source_entity_id,
        membership_digest=snapshot.membership_digest,
        memberships=memberships,
    )


def _snapshot_summaries(
    session: Session,
    stable_entity_id: int,
    target_sequence: int,
) -> tuple[StableEntitySnapshotSummary, ...]:
    rows = session.execute(
        select(StableEntitySnapshotORM, ResolverGenerationORM)
        .join(
            ResolverGenerationORM,
            ResolverGenerationORM.id == StableEntitySnapshotORM.generation_id,
        )
        .where(
            StableEntitySnapshotORM.stable_entity_id == stable_entity_id,
            ResolverGenerationORM.sequence <= target_sequence,
        )
        .order_by(ResolverGenerationORM.sequence, StableEntitySnapshotORM.id)
    ).all()
    return tuple(
        StableEntitySnapshotSummary(
            generation_uid=generation.generation_uid,
            generation_sequence=generation.sequence,
            canonical_name=snapshot.canonical_name,
            is_present=snapshot.is_present,
            source_entity_id=snapshot.source_entity_id,
            membership_digest=snapshot.membership_digest,
        )
        for snapshot, generation in rows
    )


def _lineage_views(
    session: Session,
    stable_entity_id: int,
    target_sequence: int,
) -> tuple[StableEntityLineageView, ...]:
    rows = session.execute(
        select(StableEntityLineageORM, ResolverGenerationORM)
        .join(
            ResolverGenerationORM,
            ResolverGenerationORM.id == StableEntityLineageORM.generation_id,
        )
        .where(
            (StableEntityLineageORM.from_stable_entity_id == stable_entity_id)
            | (StableEntityLineageORM.to_stable_entity_id == stable_entity_id),
            ResolverGenerationORM.sequence <= target_sequence,
        )
        .order_by(ResolverGenerationORM.sequence, StableEntityLineageORM.id)
    ).all()
    related_ids = {
        stable_id
        for lineage, _generation in rows
        for stable_id in (lineage.from_stable_entity_id, lineage.to_stable_entity_id)
    }
    entities = {
        row.id: row
        for row in session.scalars(
            select(StableEntityORM).where(StableEntityORM.id.in_(related_ids))
        )
    }
    witnesses_by_lineage: dict[int, list[StableEntityLineageWitnessView]] = defaultdict(list)
    if rows:
        lineages_by_id = {lineage.id: lineage for lineage, _generation in rows}
        witness_rows = session.execute(
            select(
                StableEntityLineageEvidenceORM.lineage_id,
                StableEntityLineageEvidenceORM.evidence_identity_id,
                EvidenceIdentityORM.fingerprint,
                StableEntityMembershipORM.evidence_identity_id,
                StableEntityMembershipORM.source_mention_id,
                StableEntityMembershipORM.generation_id,
                StableEntitySnapshotORM.generation_id,
                StableEntitySnapshotORM.stable_entity_id,
            )
            .join(
                EvidenceIdentityORM,
                EvidenceIdentityORM.id
                == StableEntityLineageEvidenceORM.evidence_identity_id,
            )
            .join(
                StableEntityMembershipORM,
                StableEntityMembershipORM.id
                == StableEntityLineageEvidenceORM.source_membership_id,
            )
            .join(
                StableEntitySnapshotORM,
                StableEntitySnapshotORM.id == StableEntityMembershipORM.snapshot_id,
            )
            .where(
                StableEntityLineageEvidenceORM.lineage_id.in_(
                    [lineage.id for lineage, _generation in rows]
                )
            )
            .order_by(
                StableEntityLineageEvidenceORM.lineage_id,
                EvidenceIdentityORM.fingerprint,
                StableEntityLineageEvidenceORM.id,
            )
        ).all()
        for (
            lineage_id,
            evidence_id,
            fingerprint,
            membership_evidence_id,
            source_mention_id,
            membership_generation_id,
            snapshot_generation_id,
            snapshot_stable_entity_id,
        ) in witness_rows:
            lineage = lineages_by_id[lineage_id]
            if membership_evidence_id != evidence_id:
                raise StableEntityInvariantError(
                    f"lineage evidence {lineage_id}/{evidence_id} points to a membership "
                    f"for different evidence {membership_evidence_id}"
                )
            if (
                membership_generation_id != lineage.generation_id
                or snapshot_generation_id != lineage.generation_id
                or snapshot_stable_entity_id != lineage.to_stable_entity_id
            ):
                raise StableEntityInvariantError(
                    f"lineage evidence {lineage_id}/{evidence_id} does not point to the "
                    "edge target's current generation membership"
                )
            witnesses_by_lineage[lineage_id].append(
                StableEntityLineageWitnessView(
                    evidence_fingerprint=fingerprint,
                    evidence_identity_id=evidence_id,
                    source_mention_id=source_mention_id,
                )
            )
        missing_witnesses = [
            lineage.id for lineage, _generation in rows if not witnesses_by_lineage[lineage.id]
        ]
        if missing_witnesses:
            raise StableEntityInvariantError(
                "stable lineage has no durable evidence witnesses: "
                f"{sorted(missing_witnesses)}"
            )
    return tuple(
        StableEntityLineageView(
            generation_uid=generation.generation_uid,
            generation_sequence=generation.sequence,
            relationship=lineage.relationship,
            from_stable_uid=entities[lineage.from_stable_entity_id].stable_uid,
            to_stable_uid=entities[lineage.to_stable_entity_id].stable_uid,
            witnesses=tuple(witnesses_by_lineage[lineage.id]),
        )
        for lineage, generation in rows
    )


def _current_target_uids(
    session: Session,
    *,
    stable_entity_id: int,
    target_generation: ResolverGenerationORM,
) -> tuple[str, ...]:
    """Follow pre-target merge/split aliases until current present snapshots.

    A continued lineage is a self-edge and does not need traversal.  A simple
    merge gives one target; a complex split/merge can intentionally return
    more than one target rather than silently picking a new identity.
    """

    snapshots = {
        row.stable_entity_id: row
        for row in session.scalars(
            select(StableEntitySnapshotORM).where(
                StableEntitySnapshotORM.generation_id == target_generation.id
            )
        )
    }
    queue: deque[int] = deque([stable_entity_id])
    visited: set[int] = set()
    target_ids: set[int] = set()
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        snapshot = snapshots.get(current)
        if snapshot is not None and snapshot.is_present:
            target_ids.add(current)
            continue

        transition_generation_id = _absence_transition_generation_id(
            session,
            stable_entity_id=current,
            target_sequence=target_generation.sequence,
        )
        if transition_generation_id is None:
            # This entity has no evidence-backed successor.  In particular,
            # do not follow an old split edge after its retained primary child
            # later disappears; that would turn historic lineage into a false
            # current redirect.
            continue

        edges = session.execute(
            select(StableEntityLineageORM)
            .where(
                StableEntityLineageORM.from_stable_entity_id == current,
                StableEntityLineageORM.relationship.in_(("merged_into", "split_into")),
                StableEntityLineageORM.generation_id == transition_generation_id,
            )
            .order_by(StableEntityLineageORM.id)
        ).scalars().all()
        queue.extend(edge.to_stable_entity_id for edge in edges)

    if not target_ids:
        return ()
    entities = {
        row.id: row
        for row in session.scalars(
            select(StableEntityORM).where(StableEntityORM.id.in_(target_ids))
        )
    }
    return tuple(sorted(entities[stable_id].stable_uid for stable_id in target_ids))


def _absence_transition_generation_id(
    session: Session,
    *,
    stable_entity_id: int,
    target_sequence: int,
) -> int | None:
    """Find the generation where this now-absent entity lost presence.

    Lineage only describes the transition that produced an absence.  Looking
    at every older merge/split edge would incorrectly redirect an entity that
    subsequently disappeared because all of its current source evidence was
    retracted.  Full snapshots make this boundary exact and inexpensive to
    explain, even though it is a little more storage than a range model.
    """

    rows = session.execute(
        select(StableEntitySnapshotORM.is_present, ResolverGenerationORM.id)
        .join(
            ResolverGenerationORM,
            ResolverGenerationORM.id == StableEntitySnapshotORM.generation_id,
        )
        .where(
            StableEntitySnapshotORM.stable_entity_id == stable_entity_id,
            ResolverGenerationORM.sequence <= target_sequence,
        )
        .order_by(ResolverGenerationORM.sequence)
    ).all()
    last_present_index: int | None = None
    for index, (is_present, _generation_id) in enumerate(rows):
        if is_present:
            last_present_index = index
    if last_present_index is None or last_present_index + 1 >= len(rows):
        return None
    _is_present, transition_generation_id = rows[last_present_index + 1]
    return transition_generation_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
