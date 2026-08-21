"""Durable document/evidence identity and constraint remapping (M4.2a).

Raw document, mention, and resolver rows remain the existing versioned
pipeline facts.  This module adds a small, explicit identity layer beside
them: a document UID, an extractor-independent fingerprint for an exact
source span, and human constraints anchored to those fingerprints.

It deliberately does *not* choose stable entity identities or alter resolver
output.  ``remap_resolution_constraints`` is a read-only projection that a
later resolver-generation checkpoint can consume after its lineage semantics
are approved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
import uuid
from typing import Iterable, Mapping

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from src.store.blob import BlobStore, sha256_text
from src.store.documents import resolve_document_text
from src.store.orm import (
    DocumentIdentityORM,
    DocumentORM,
    EvidenceIdentityORM,
    ExtractorVersionORM,
    MentionEvidenceIdentityORM,
    MentionORM,
    ProvenanceORM,
    ResolutionConstraintORM,
    ResolutionDecisionORM,
)

DOCUMENT_IDENTITY_VERSION = "1"
EVIDENCE_IDENTITY_VERSION = "1"
MENTION_EVIDENCE_MAPPER_VERSION = "1"
_ADOPTION_PAGE_SIZE = 500

# Kept constant so a resumed backfill assigns the same UID to an old row. New
# documents receive uuid4 values at write time and never consult this namespace.
_LEGACY_DOCUMENT_UID_NAMESPACE = uuid.UUID("d81e8acb-680d-438d-8a2d-bb2fae3cdb77")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE_TAG_RE = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]{2,8})*$")


class IdentityInvariantError(ValueError):
    """The identity layer cannot safely represent the supplied source fact."""


class IdentityAdoptionError(RuntimeError):
    """A planned identity adoption has unsafe rows and must not write."""

    def __init__(self, report: IdentityAdoptionReport) -> None:
        self.report = report
        super().__init__("identity adoption has unresolved validation errors")


def canonical_language(language: str) -> str:
    """Validate and canonicalize a compact BCP-47 language tag.

    ``und`` is deliberately allowed for an explicitly unknown language.  It
    is never silently selected by the backfill; an operator must provide a
    language mapping/default when adopting old mention rows.
    """

    if not isinstance(language, str):
        raise IdentityInvariantError("language must be a string")
    value = language.strip().lower()
    if value == "und" or _LANGUAGE_TAG_RE.fullmatch(value):
        return value
    raise IdentityInvariantError(
        "language must be a BCP-47-like tag such as 'ar', 'fa', or 'und'"
    )


def legacy_document_uid(document_id: int) -> str:
    """Deterministic UID used only when adopting a pre-M4.2 document row."""

    if document_id <= 0:
        raise IdentityInvariantError("document id must be positive before identity adoption")
    return str(
        uuid.uuid5(
            _LEGACY_DOCUMENT_UID_NAMESPACE,
            f"arabic-osint-intelligence-platform/document/{document_id}",
        )
    )


def evidence_fingerprint(
    *,
    document_uid: str,
    source_text_sha256: str,
    start_offset: int,
    end_offset: int,
    object_type: str,
    language: str,
) -> str:
    """Return the durable identity of one exact source-text evidence span.

    The canonical JSON shape makes future algorithm versions explicit and
    testable.  Extractor version is intentionally absent: it remains on the
    raw mention/provenance rows so an exact version-bump replacement maps to
    the same evidence identity rather than to a new constraint endpoint.
    """

    uid = _canonical_document_uid(document_uid)
    source_hash = _canonical_sha256(source_text_sha256)
    if start_offset < 0 or end_offset <= start_offset:
        raise IdentityInvariantError("evidence offsets must satisfy 0 <= start < end")
    if not isinstance(object_type, str) or not object_type.strip():
        raise IdentityInvariantError("evidence object_type must be a non-empty string")

    payload = {
        "document_uid": uid,
        "end_offset": end_offset,
        "identity_version": EVIDENCE_IDENTITY_VERSION,
        "language": canonical_language(language),
        "object_type": object_type,
        "source_text_sha256": source_hash,
        "start_offset": start_offset,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def ensure_document_identity(
    session: Session,
    document: DocumentORM,
    *,
    document_uid: str | None = None,
) -> DocumentIdentityORM:
    """Get or create the immutable UID mapping for one document row."""

    if document.id is None:
        raise IdentityInvariantError("document must be flushed before assigning an identity")
    supplied_uid = _canonical_document_uid(document_uid) if document_uid else None
    existing = session.scalar(
        select(DocumentIdentityORM).where(DocumentIdentityORM.document_id == document.id)
    )
    if existing is not None:
        if supplied_uid is not None and existing.document_uid != supplied_uid:
            raise IdentityInvariantError(
                f"document {document.id} already has UID {existing.document_uid}, "
                f"not {supplied_uid}"
            )
        return existing

    row = DocumentIdentityORM(
        document_id=document.id,
        document_uid=supplied_uid or str(uuid.uuid4()),
        identity_version=DOCUMENT_IDENTITY_VERSION,
    )
    session.add(row)
    session.flush()
    return row


def ensure_mention_evidence_identity(
    session: Session,
    *,
    document: DocumentORM,
    mention: MentionORM,
    language: str,
    source_text_sha256: str | None = None,
    document_uid: str | None = None,
    mapper_version: str = MENTION_EVIDENCE_MAPPER_VERSION,
) -> EvidenceIdentityORM:
    """Map a raw mention to its exact durable evidence identity.

    The caller owns source-text validation.  Normal mention creation already
    verifies P2 against the original document text; the adoption path repeats
    that validation before calling this function.  The hash is kept here so a
    fingerprint never relies on a mutable/implicit text lookup.
    """

    if document.id is None or mention.id is None:
        raise IdentityInvariantError("document and mention must be flushed before identity mapping")
    if mention.document_id != document.id:
        raise IdentityInvariantError(
            f"mention {mention.id} belongs to document {mention.document_id}, not {document.id}"
        )
    source_hash = _canonical_sha256(source_text_sha256 or document.text_sha256 or "")
    normalized_language = canonical_language(language)
    document_identity = ensure_document_identity(
        session,
        document,
        document_uid=document_uid,
    )
    expected_fingerprint = evidence_fingerprint(
        document_uid=document_identity.document_uid,
        source_text_sha256=source_hash,
        start_offset=mention.start_offset,
        end_offset=mention.end_offset,
        object_type=mention.object_type,
        language=normalized_language,
    )

    existing_mapping = session.get(MentionEvidenceIdentityORM, mention.id)
    if existing_mapping is not None:
        existing_evidence = session.get(EvidenceIdentityORM, existing_mapping.evidence_identity_id)
        if existing_evidence is None:
            raise IdentityInvariantError(
                f"mention {mention.id} maps to missing evidence {existing_mapping.evidence_identity_id}"
            )
        _assert_evidence_matches(
            existing_evidence,
            document_identity=document_identity,
            fingerprint=expected_fingerprint,
            source_text_sha256=source_hash,
            mention=mention,
            language=normalized_language,
        )
        return existing_evidence

    evidence = session.scalar(
        select(EvidenceIdentityORM).where(
            EvidenceIdentityORM.fingerprint == expected_fingerprint
        )
    )
    if evidence is None:
        evidence = EvidenceIdentityORM(
            document_identity_id=document_identity.id,
            fingerprint=expected_fingerprint,
            identity_version=EVIDENCE_IDENTITY_VERSION,
            source_text_sha256=source_hash,
            start_offset=mention.start_offset,
            end_offset=mention.end_offset,
            object_type=mention.object_type,
            language=normalized_language,
        )
        session.add(evidence)
        session.flush()
    else:
        _assert_evidence_matches(
            evidence,
            document_identity=document_identity,
            fingerprint=expected_fingerprint,
            source_text_sha256=source_hash,
            mention=mention,
            language=normalized_language,
        )

    session.add(
        MentionEvidenceIdentityORM(
            mention_id=mention.id,
            evidence_identity_id=evidence.id,
            mapper_version=mapper_version,
        )
    )
    # Each raw mention version that maps here remains visible in the evidence
    # identity's P1 chain; the identity table itself stores no source snippet.
    session.add(
        ProvenanceORM(
            target_table="evidence_identities",
            target_id=evidence.id,
            document_id=document.id,
            mention_id=mention.id,
            extractor_version_id=mention.extractor_version_id,
        )
    )
    session.flush()
    return evidence


def record_resolution_constraint(
    session: Session,
    decision: ResolutionDecisionORM,
) -> ResolutionConstraintORM:
    """Dual-write the durable form of an append-only resolution decision.

    Existing raw decisions remain unchanged.  A legacy decision without
    evidence mappings is refused rather than assigned an inferred language or
    a guessed span; run the explicit adoption command first.
    """

    if decision.id is None:
        raise IdentityInvariantError("resolution decision must be flushed before constraint mapping")
    existing = session.scalar(
        select(ResolutionConstraintORM).where(
            ResolutionConstraintORM.source_decision_id == decision.id
        )
    )
    if existing is not None:
        return existing

    left_evidence = _evidence_for_mention(session, decision.left_mention_id)
    right_evidence = _evidence_for_mention(session, decision.right_mention_id)
    endpoints = sorted(
        (left_evidence, right_evidence), key=lambda row: (row.fingerprint, row.id)
    )
    prior_constraint_id: int | None = None
    if decision.supersedes_id is not None:
        prior_constraint = session.scalar(
            select(ResolutionConstraintORM).where(
                ResolutionConstraintORM.source_decision_id == decision.supersedes_id
            )
        )
        if prior_constraint is None:
            raise IdentityInvariantError(
                f"decision {decision.id} supersedes {decision.supersedes_id}, but its "
                "durable constraint is missing; complete identity adoption first"
            )
        prior_constraint_id = prior_constraint.id

    row = ResolutionConstraintORM(
        source_decision_id=decision.id,
        left_evidence_identity_id=endpoints[0].id,
        right_evidence_identity_id=endpoints[1].id,
        decision=decision.decision,
        supersedes_constraint_id=prior_constraint_id,
    )
    session.add(row)
    session.flush()

    for mention_id in (decision.left_mention_id, decision.right_mention_id):
        mention = session.get(MentionORM, mention_id)
        if mention is None:
            raise IdentityInvariantError(
                f"resolution decision {decision.id} references missing mention {mention_id}"
            )
        session.add(
            ProvenanceORM(
                target_table="resolution_constraints",
                target_id=row.id,
                document_id=mention.document_id,
                mention_id=mention.id,
                extractor_version_id=decision.extractor_version_id,
            )
        )
    session.flush()
    return row


def active_resolution_constraints(session: Session) -> list[ResolutionConstraintORM]:
    """Return unsuperseded durable constraints without recency overwrite."""

    rows = list(
        session.scalars(select(ResolutionConstraintORM).order_by(ResolutionConstraintORM.id))
    )
    superseded_ids = {
        row.supersedes_constraint_id
        for row in rows
        if row.supersedes_constraint_id is not None
    }
    return [row for row in rows if row.id not in superseded_ids]


@dataclass(frozen=True, slots=True)
class ConstraintRemap:
    """Read-only current evidence endpoints for one active durable constraint."""

    constraint_id: int
    source_decision_id: int
    decision: str
    left_evidence_fingerprint: str
    right_evidence_fingerprint: str
    left_mention_ids: tuple[int, ...]
    right_mention_ids: tuple[int, ...]
    status: str
    reason: str
    conflicting_constraint_ids: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "constraint_id": self.constraint_id,
            "source_decision_id": self.source_decision_id,
            "decision": self.decision,
            "left_evidence_fingerprint": self.left_evidence_fingerprint,
            "right_evidence_fingerprint": self.right_evidence_fingerprint,
            "left_mention_ids": list(self.left_mention_ids),
            "right_mention_ids": list(self.right_mention_ids),
            "status": self.status,
            "reason": self.reason,
            "conflicting_constraint_ids": list(self.conflicting_constraint_ids),
        }


def remap_resolution_constraints(session: Session) -> list[ConstraintRemap]:
    """Project active durable constraints onto current live raw mentions.

    Exact fingerprints remap automatically.  A span shift has no matching
    fingerprint and is reported as ``unresolved`` instead of being matched by
    text similarity.  Opposing active decisions for one durable pair report
    ``conflict``; neither recency nor raw mention ids silently choose a side.
    """

    constraints = active_resolution_constraints(session)
    if not constraints:
        return []

    evidence_ids = {
        endpoint_id
        for row in constraints
        for endpoint_id in (row.left_evidence_identity_id, row.right_evidence_identity_id)
    }
    evidence_by_id = {
        row.id: row
        for row in session.scalars(
            select(EvidenceIdentityORM).where(EvidenceIdentityORM.id.in_(evidence_ids))
        )
    }
    if evidence_ids.difference(evidence_by_id):
        missing = sorted(evidence_ids.difference(evidence_by_id))
        raise IdentityInvariantError(f"constraints refer to missing evidence identities: {missing}")

    live_mention_ids: dict[int, list[int]] = defaultdict(list)
    live_rows = session.execute(
        select(MentionEvidenceIdentityORM.evidence_identity_id, MentionORM.id)
        .join(MentionORM, MentionORM.id == MentionEvidenceIdentityORM.mention_id)
        .join(DocumentORM, DocumentORM.id == MentionORM.document_id)
        .where(
            MentionEvidenceIdentityORM.evidence_identity_id.in_(evidence_ids),
            MentionORM.retracted.is_(False),
            DocumentORM.retracted.is_(False),
        )
        .order_by(MentionEvidenceIdentityORM.evidence_identity_id, MentionORM.id)
    ).all()
    for evidence_id, mention_id in live_rows:
        live_mention_ids[evidence_id].append(mention_id)

    constraints_by_pair: dict[tuple[str, str], list[ResolutionConstraintORM]] = defaultdict(list)
    for row in constraints:
        left = evidence_by_id[row.left_evidence_identity_id].fingerprint
        right = evidence_by_id[row.right_evidence_identity_id].fingerprint
        constraints_by_pair[(left, right)].append(row)

    remaps: list[ConstraintRemap] = []
    for row in constraints:
        left = evidence_by_id[row.left_evidence_identity_id]
        right = evidence_by_id[row.right_evidence_identity_id]
        pair_rows = constraints_by_pair[(left.fingerprint, right.fingerprint)]
        decisions = {pair_row.decision for pair_row in pair_rows}
        conflict_ids = tuple(
            pair_row.id for pair_row in pair_rows if len(decisions) > 1
        )
        left_ids = tuple(live_mention_ids[left.id])
        right_ids = tuple(live_mention_ids[right.id])
        missing = []
        if not left_ids:
            missing.append("left")
        if not right_ids:
            missing.append("right")

        if len(decisions) > 1 or (
            row.decision == "different" and left.fingerprint == right.fingerprint
        ):
            status = "conflict"
            reason = "opposing_active_constraints" if len(decisions) > 1 else "self_evidence_cannot_link"
        elif missing:
            status = "unresolved"
            reason = "missing_live_" + "_and_".join(missing) + "_evidence"
        else:
            status = "remapped"
            reason = "exact_live_evidence_match"

        remaps.append(
            ConstraintRemap(
                constraint_id=row.id,
                source_decision_id=row.source_decision_id,
                decision=row.decision,
                left_evidence_fingerprint=left.fingerprint,
                right_evidence_fingerprint=right.fingerprint,
                left_mention_ids=left_ids,
                right_mention_ids=right_ids,
                status=status,
                reason=reason,
                conflicting_constraint_ids=conflict_ids,
            )
        )
    return remaps


@dataclass(frozen=True, slots=True)
class IdentityAdoptionIssue:
    kind: str
    row_id: int
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "row_id": self.row_id, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class IdentityAdoptionReport:
    documents_total: int
    document_identities_existing: int
    document_identities_missing: int
    mentions_total: int
    mention_mappings_existing: int
    mention_mappings_missing: int
    decisions_total: int
    constraints_existing: int
    constraints_missing: int
    errors: tuple[IdentityAdoptionIssue, ...]
    remap_status_counts: dict[str, int]
    applied: dict[str, int] | None = None

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self, *, mode: str) -> dict[str, object]:
        return {
            "mode": mode,
            "ready": self.ready,
            "documents": {
                "total": self.documents_total,
                "identities_existing": self.document_identities_existing,
                "identities_missing": self.document_identities_missing,
            },
            "mentions": {
                "total": self.mentions_total,
                "mappings_existing": self.mention_mappings_existing,
                "mappings_missing": self.mention_mappings_missing,
            },
            "decisions": {
                "total": self.decisions_total,
                "constraints_existing": self.constraints_existing,
                "constraints_missing": self.constraints_missing,
            },
            "remap_status_counts": dict(sorted(self.remap_status_counts.items())),
            "errors": [issue.as_dict() for issue in self.errors],
            "applied": dict(sorted(self.applied.items())) if self.applied is not None else None,
        }


@dataclass(frozen=True, slots=True)
class _AdoptionMentionInput:
    mention_id: int
    document_id: int
    source_text_sha256: str
    language: str


@dataclass(frozen=True, slots=True)
class _AdoptionDocument:
    """The loaded fields needed to validate one legacy document's text."""

    id: int
    text: str | None
    text_blob_key: str | None
    text_sha256: str | None
    text_length: int | None


@dataclass(frozen=True, slots=True)
class _AdoptionMention:
    """A detached raw mention used by the read-only adoption planner."""

    id: int
    document_id: int
    text: str
    start_offset: int
    end_offset: int
    object_type: str
    extractor_version_id: int


@dataclass(frozen=True, slots=True)
class _AdoptionDecision:
    """The decision fields needed to prove both endpoints are mappable."""

    id: int
    left_mention_id: int
    right_mention_id: int


@dataclass(frozen=True, slots=True)
class _PendingAdoptionMention:
    """A mention whose cheap checks passed before source validation."""

    mention: _AdoptionMention
    document: _AdoptionDocument
    language: str


@dataclass(frozen=True, slots=True)
class _IdentityAdoptionSnapshot:
    """All database state the slow, read-only blob validation needs.

    No ORM instance escapes this snapshot.  The CLI can therefore release its
    database connection before fetching source blobs without expiring fields
    midway through validation.
    """

    documents: tuple[_AdoptionDocument, ...]
    document_identity_ids: frozenset[int]
    mentions: tuple[_AdoptionMention, ...]
    mapped_mention_ids: frozenset[int]
    extractor_names_by_id: dict[int, str]
    decisions: tuple[_AdoptionDecision, ...]
    constrained_decision_ids: frozenset[int]
    remap_status_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class _IdentityAdoptionPlan:
    report: IdentityAdoptionReport
    document_ids: tuple[int, ...]
    mention_inputs: tuple[_AdoptionMentionInput, ...]
    decision_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _PreparedAdoptionMention:
    """One current raw mention matched to a source-validated plan input.

    The planner deliberately holds detached scalar values while it reads slow
    source blobs.  The writer reloads these ORM rows in bounded queries before
    it changes anything, then uses this small record to keep each evidence
    signature explicit without re-querying the database for every mention.
    """

    item: _AdoptionMentionInput
    document: DocumentORM
    mention: MentionORM
    document_identity: DocumentIdentityORM
    source_text_sha256: str
    language: str
    fingerprint: str


def plan_identity_adoption(
    session: Session,
    *,
    blob_store: BlobStore,
    extractor_languages: Mapping[str, str] | None = None,
    default_language: str | None = None,
    valid_object_types: Iterable[str] | None = None,
    release_database_connection: bool = False,
) -> IdentityAdoptionReport:
    """Read-only validation/report for an idempotent legacy identity adoption.

    ``release_database_connection`` is for a clean, dedicated audit session
    such as the production CLI.  It snapshots every required database row,
    returns that connection before potentially slow R2 reads, and then
    validates the detached snapshot.  It deliberately refuses a caller-owned
    transaction so this convenience can never roll back uncommitted work.
    """

    if release_database_connection and session.in_transaction():
        raise IdentityInvariantError(
            "cannot release a caller-owned transaction during identity adoption planning"
        )

    snapshot = _load_identity_adoption_snapshot(session)
    if release_database_connection:
        session.rollback()

    return _plan_identity_adoption_snapshot(
        snapshot,
        blob_store=blob_store,
        extractor_languages=extractor_languages,
        default_language=default_language,
        valid_object_types=valid_object_types,
    ).report


def apply_identity_adoption(
    session: Session,
    *,
    blob_store: BlobStore,
    extractor_languages: Mapping[str, str] | None = None,
    default_language: str | None = None,
    valid_object_types: Iterable[str] | None = None,
) -> IdentityAdoptionReport:
    """Write one safe, resumable identity adoption after a clean plan."""

    plan = _plan_identity_adoption(
        session,
        blob_store=blob_store,
        extractor_languages=extractor_languages,
        default_language=default_language,
        valid_object_types=valid_object_types,
    )
    if not plan.report.ready:
        raise IdentityAdoptionError(plan.report)

    _write_identity_adoption_plan(session, plan)

    # Keep a second full source/span validation inside the caller's one
    # transaction.  The bulk writer below changes only how rows are loaded
    # and flushed; it does not turn a successful audit into permission to
    # trust stale source facts.
    session.flush()
    after = _plan_identity_adoption(
        session,
        blob_store=blob_store,
        extractor_languages=extractor_languages,
        default_language=default_language,
        valid_object_types=valid_object_types,
    ).report
    return IdentityAdoptionReport(
        documents_total=after.documents_total,
        document_identities_existing=after.document_identities_existing,
        document_identities_missing=after.document_identities_missing,
        mentions_total=after.mentions_total,
        mention_mappings_existing=after.mention_mappings_existing,
        mention_mappings_missing=after.mention_mappings_missing,
        decisions_total=after.decisions_total,
        constraints_existing=after.constraints_existing,
        constraints_missing=after.constraints_missing,
        errors=after.errors,
        remap_status_counts=after.remap_status_counts,
        applied={
            "document_identities": plan.report.document_identities_missing,
            "mention_mappings": plan.report.mention_mappings_missing,
            "resolution_constraints": plan.report.constraints_missing,
        },
    )


def _write_identity_adoption_plan(session: Session, plan: _IdentityAdoptionPlan) -> None:
    """Materialize a ready adoption plan without per-row database round trips.

    ``apply_identity_adoption`` is called by the CLI inside ``session.begin()``.
    This helper uses normal session-bound SQL statements rather than a
    fast-but-detached bulk API: a cancelled or failed run still rolls back
    document identities, evidence, mappings, and provenance together.  There
    are only a few dependency barriers:

    1. document identities are inserted, then fetched once before evidence
       references them;
    2. evidence is inserted, then fetched once before mappings/provenance
       reference it; and
    3. a superseding constraint may need its new predecessor's ID.

    The common 7,854-mapping production case has no legacy decisions, so it
    performs bounded multi-row inserts, rather than flushing once per
    mention.
    """

    document_ids_for_mentions = _ordered_unique(
        item.document_id for item in plan.mention_inputs
    )
    document_ids_to_load = _ordered_unique(
        (*plan.document_ids, *document_ids_for_mentions)
    )
    documents_by_id = _rows_by_id(
        session,
        DocumentORM,
        DocumentORM.id,
        document_ids_to_load,
    )

    # Preserve the original writer's first phase and its deterministic
    # UUID5 adoption rule.  Existing, concurrently-created identities are
    # accepted only when they have that same durable value.
    document_identities_by_document_id = {
        row.document_id: row
        for row in _rows_for_values(
            session,
            DocumentIdentityORM,
            DocumentIdentityORM.document_id,
            document_ids_to_load,
        )
    }
    new_document_identity_rows: list[dict[str, object]] = []
    new_document_identity_ids: list[int] = []
    for document_id in plan.document_ids:
        document = documents_by_id.get(document_id)
        if document is None:
            raise IdentityInvariantError(f"document {document_id} disappeared during adoption")
        expected_uid = legacy_document_uid(document_id)
        existing = document_identities_by_document_id.get(document_id)
        if existing is not None:
            if existing.document_uid != expected_uid:
                raise IdentityInvariantError(
                    f"document {document_id} already has UID {existing.document_uid}, "
                    f"not {expected_uid}"
                )
            continue
        new_document_identity_rows.append(
            {
                "document_id": document_id,
                "document_uid": expected_uid,
                "identity_version": DOCUMENT_IDENTITY_VERSION,
            }
        )
        new_document_identity_ids.append(document_id)

    if new_document_identity_rows:
        _insert_adoption_rows(
            session,
            DocumentIdentityORM,
            new_document_identity_rows,
        )
        document_identities_by_document_id.update(
            {
                row.document_id: row
                for row in _rows_for_values(
                    session,
                    DocumentIdentityORM,
                    DocumentIdentityORM.document_id,
                    tuple(new_document_identity_ids),
                )
            }
        )

    # A document identity normally appears either in the original plan or in
    # the table above.  This narrow fallback preserves the sanctioned writer's
    # behavior if another actor removed an identity after planning; it is not
    # on the normal bulk path and cannot turn into an N+1 query loop.
    for document_id in document_ids_for_mentions:
        if document_id in document_identities_by_document_id:
            continue
        document = documents_by_id.get(document_id)
        if document is None:
            continue
        document_identities_by_document_id[document_id] = ensure_document_identity(
            session,
            document,
        )

    planned_mention_ids = _ordered_unique(item.mention_id for item in plan.mention_inputs)
    mentions_by_id = _rows_by_id(
        session,
        MentionORM,
        MentionORM.id,
        planned_mention_ids,
    )
    prepared_mentions: list[_PreparedAdoptionMention] = []
    for item in plan.mention_inputs:
        document = documents_by_id.get(item.document_id)
        mention = mentions_by_id.get(item.mention_id)
        if document is None or mention is None:
            raise IdentityInvariantError(
                f"mention {item.mention_id} or document {item.document_id} disappeared during adoption"
            )
        if mention.document_id != document.id:
            raise IdentityInvariantError(
                f"mention {mention.id} belongs to document {mention.document_id}, not {document.id}"
            )
        document_identity = document_identities_by_document_id.get(document.id)
        if document_identity is None:
            # The fallback above covers all normal paths.  Keep the old
            # sanctioned behavior for this impossible-without-a-race state.
            document_identity = ensure_document_identity(session, document)
            document_identities_by_document_id[document.id] = document_identity
        source_text_sha256 = _canonical_sha256(item.source_text_sha256)
        language = canonical_language(item.language)
        prepared_mentions.append(
            _PreparedAdoptionMention(
                item=item,
                document=document,
                mention=mention,
                document_identity=document_identity,
                source_text_sha256=source_text_sha256,
                language=language,
                fingerprint=evidence_fingerprint(
                    document_uid=document_identity.document_uid,
                    source_text_sha256=source_text_sha256,
                    start_offset=mention.start_offset,
                    end_offset=mention.end_offset,
                    object_type=mention.object_type,
                    language=language,
                ),
            )
        )

    # Read all decision rows and their endpoints before adding mappings.  The
    # validation/exception order is still controlled by the writing phases
    # below; these are only bounded prefetches.
    decisions_by_id = _rows_by_id(
        session,
        ResolutionDecisionORM,
        ResolutionDecisionORM.id,
        plan.decision_ids,
    )
    decision_endpoint_mention_ids = _ordered_unique(
        mention_id
        for decision in decisions_by_id.values()
        for mention_id in (decision.left_mention_id, decision.right_mention_id)
    )
    all_mapping_mention_ids = _ordered_unique(
        (*planned_mention_ids, *decision_endpoint_mention_ids)
    )
    if decision_endpoint_mention_ids:
        mentions_by_id.update(
            _rows_by_id(
                session,
                MentionORM,
                MentionORM.id,
                decision_endpoint_mention_ids,
            )
        )

    mappings_by_mention_id = {
        row.mention_id: row
        for row in _rows_for_values(
            session,
            MentionEvidenceIdentityORM,
            MentionEvidenceIdentityORM.mention_id,
            all_mapping_mention_ids,
        )
    }
    existing_evidence_ids = _ordered_unique(
        mapping.evidence_identity_id for mapping in mappings_by_mention_id.values()
    )
    evidence_by_id = _rows_by_id(
        session,
        EvidenceIdentityORM,
        EvidenceIdentityORM.id,
        existing_evidence_ids,
    )
    evidence_by_fingerprint = {
        row.fingerprint: row for row in evidence_by_id.values()
    }
    expected_fingerprints = _ordered_unique(
        prepared.fingerprint for prepared in prepared_mentions
    )
    for evidence in _rows_for_values(
        session,
        EvidenceIdentityORM,
        EvidenceIdentityORM.fingerprint,
        expected_fingerprints,
    ):
        evidence_by_id[evidence.id] = evidence
        evidence_by_fingerprint[evidence.fingerprint] = evidence

    # A decision may supersede either an already-adopted constraint or a
    # predecessor that this plan creates earlier in append order.  Prefetch
    # both kinds so no decision uses scalar queries in its write loop.
    constraint_source_decision_ids = _ordered_unique(
        (
            *plan.decision_ids,
            *(
                decision.supersedes_id
                for decision in decisions_by_id.values()
                if decision.supersedes_id is not None
            ),
        )
    )
    constraints_by_source_decision_id = {
        row.source_decision_id: row
        for row in _rows_for_values(
            session,
            ResolutionConstraintORM,
            ResolutionConstraintORM.source_decision_id,
            constraint_source_decision_ids,
        )
    }

    # Recreate the normal writer's evidence assertions before adding a single
    # mapping.  Reused evidence is still checked against the current raw row;
    # only the database loading and flush cadence have changed.
    evidence_by_mention_id: dict[int, EvidenceIdentityORM] = {}
    prepared_needing_mapping: list[_PreparedAdoptionMention] = []
    new_evidence: list[EvidenceIdentityORM] = []
    for prepared in prepared_mentions:
        mapping = mappings_by_mention_id.get(prepared.mention.id)
        if mapping is not None:
            evidence = evidence_by_id.get(mapping.evidence_identity_id)
            if evidence is None:
                raise IdentityInvariantError(
                    f"mention {prepared.mention.id} maps to missing evidence "
                    f"{mapping.evidence_identity_id}"
                )
            _assert_evidence_matches(
                evidence,
                document_identity=prepared.document_identity,
                fingerprint=prepared.fingerprint,
                source_text_sha256=prepared.source_text_sha256,
                mention=prepared.mention,
                language=prepared.language,
            )
            continue

        evidence = evidence_by_fingerprint.get(prepared.fingerprint)
        if evidence is None:
            evidence = EvidenceIdentityORM(
                document_identity_id=prepared.document_identity.id,
                fingerprint=prepared.fingerprint,
                identity_version=EVIDENCE_IDENTITY_VERSION,
                source_text_sha256=prepared.source_text_sha256,
                start_offset=prepared.mention.start_offset,
                end_offset=prepared.mention.end_offset,
                object_type=prepared.mention.object_type,
                language=prepared.language,
            )
            new_evidence.append(evidence)
            evidence_by_fingerprint[evidence.fingerprint] = evidence
        else:
            _assert_evidence_matches(
                evidence,
                document_identity=prepared.document_identity,
                fingerprint=prepared.fingerprint,
                source_text_sha256=prepared.source_text_sha256,
                mention=prepared.mention,
                language=prepared.language,
            )
        prepared_needing_mapping.append(prepared)

    if new_evidence:
        _insert_adoption_rows(
            session,
            EvidenceIdentityORM,
            [
                {
                    "document_identity_id": evidence.document_identity_id,
                    "fingerprint": evidence.fingerprint,
                    "identity_version": evidence.identity_version,
                    "source_text_sha256": evidence.source_text_sha256,
                    "start_offset": evidence.start_offset,
                    "end_offset": evidence.end_offset,
                    "object_type": evidence.object_type,
                    "language": evidence.language,
                }
                for evidence in new_evidence
            ],
        )
        for evidence in _rows_for_values(
            session,
            EvidenceIdentityORM,
            EvidenceIdentityORM.fingerprint,
            _ordered_unique(evidence.fingerprint for evidence in new_evidence),
        ):
            evidence_by_id[evidence.id] = evidence
            evidence_by_fingerprint[evidence.fingerprint] = evidence

    for prepared in prepared_mentions:
        evidence_by_mention_id[prepared.mention.id] = evidence_by_fingerprint[
            prepared.fingerprint
        ]

    # These rows carry no generated IDs that the remainder of this writer
    # needs, so execute them in bounded multi-row statements rather than
    # creating thousands of pending ORM objects for one flush to sort.
    _insert_adoption_rows(
        session,
        MentionEvidenceIdentityORM,
        [
            {
                "mention_id": prepared.mention.id,
                "evidence_identity_id": evidence_by_mention_id[prepared.mention.id].id,
                "mapper_version": MENTION_EVIDENCE_MAPPER_VERSION,
            }
            for prepared in prepared_needing_mapping
        ],
    )
    _insert_adoption_rows(
        session,
        ProvenanceORM,
        [
            {
                "target_table": "evidence_identities",
                "target_id": evidence_by_mention_id[prepared.mention.id].id,
                "document_id": prepared.document.id,
                "mention_id": prepared.mention.id,
                "extractor_version_id": prepared.mention.extractor_version_id,
            }
            for prepared in prepared_needing_mapping
        ],
    )

    def evidence_for_mention(mention_id: int) -> EvidenceIdentityORM:
        prepared_evidence = evidence_by_mention_id.get(mention_id)
        if prepared_evidence is not None:
            return prepared_evidence
        mapping = mappings_by_mention_id.get(mention_id)
        if mapping is None:
            raise IdentityInvariantError(
                f"mention {mention_id} has no durable evidence mapping; "
                "run identity adoption first"
            )
        evidence = evidence_by_id.get(mapping.evidence_identity_id)
        if evidence is None:
            raise IdentityInvariantError(
                f"mention {mention_id} maps to missing evidence "
                f"{mapping.evidence_identity_id}"
            )
        return evidence

    # Constraint rows must be appended in decision order.  In the unusual
    # case where a newly-created decision supersedes another new decision,
    # flush just that predecessor batch to obtain its ID before inserting the
    # child.  Independent decisions remain batched together.
    pending_constraints: dict[int, ResolutionConstraintORM] = {}
    new_constraint_provenance: list[
        tuple[ResolutionConstraintORM, MentionORM, MentionORM, ResolutionDecisionORM]
    ] = []

    def flush_pending_constraints() -> None:
        if not pending_constraints:
            return
        session.flush()
        constraints_by_source_decision_id.update(pending_constraints)
        pending_constraints.clear()

    for decision_id in plan.decision_ids:
        decision = decisions_by_id.get(decision_id)
        if decision is None:
            raise IdentityInvariantError(f"decision {decision_id} disappeared during adoption")
        if decision_id in constraints_by_source_decision_id:
            continue

        left_evidence = evidence_for_mention(decision.left_mention_id)
        right_evidence = evidence_for_mention(decision.right_mention_id)
        endpoints = sorted(
            (left_evidence, right_evidence), key=lambda row: (row.fingerprint, row.id)
        )
        supersedes_constraint_id: int | None = None
        if decision.supersedes_id is not None:
            predecessor = constraints_by_source_decision_id.get(decision.supersedes_id)
            if predecessor is None and decision.supersedes_id in pending_constraints:
                flush_pending_constraints()
                predecessor = constraints_by_source_decision_id.get(decision.supersedes_id)
            if predecessor is None:
                raise IdentityInvariantError(
                    f"decision {decision.id} supersedes {decision.supersedes_id}, but its "
                    "durable constraint is missing; complete identity adoption first"
                )
            supersedes_constraint_id = predecessor.id

        left_mention = mentions_by_id.get(decision.left_mention_id)
        right_mention = mentions_by_id.get(decision.right_mention_id)
        if left_mention is None:
            raise IdentityInvariantError(
                f"resolution decision {decision.id} references missing mention "
                f"{decision.left_mention_id}"
            )
        if right_mention is None:
            raise IdentityInvariantError(
                f"resolution decision {decision.id} references missing mention "
                f"{decision.right_mention_id}"
            )

        constraint = ResolutionConstraintORM(
            source_decision_id=decision.id,
            left_evidence_identity_id=endpoints[0].id,
            right_evidence_identity_id=endpoints[1].id,
            decision=decision.decision,
            supersedes_constraint_id=supersedes_constraint_id,
        )
        session.add(constraint)
        pending_constraints[decision.id] = constraint
        new_constraint_provenance.append(
            (constraint, left_mention, right_mention, decision)
        )

    flush_pending_constraints()

    for constraint, left_mention, right_mention, decision in new_constraint_provenance:
        session.add_all(
            [
                ProvenanceORM(
                    target_table="resolution_constraints",
                    target_id=constraint.id,
                    document_id=left_mention.document_id,
                    mention_id=left_mention.id,
                    extractor_version_id=decision.extractor_version_id,
                ),
                ProvenanceORM(
                    target_table="resolution_constraints",
                    target_id=constraint.id,
                    document_id=right_mention.document_id,
                    mention_id=right_mention.id,
                    extractor_version_id=decision.extractor_version_id,
                ),
            ]
        )

    # This flush is only for ORM-managed constraint provenance.  The
    # high-cardinality document/evidence/mapping rows above already used
    # bounded INSERT statements.  The caller's enclosing transaction makes
    # every preceding barrier all-or-nothing.
    session.flush()


def _ordered_unique(values: Iterable[int]) -> tuple[int, ...]:
    """Return integer IDs once, preserving the planner's deterministic order."""

    return tuple(dict.fromkeys(values))


def _rows_for_values(
    session: Session,
    model: type[object],
    column: object,
    values: tuple[int, ...] | tuple[str, ...],
) -> list[object]:
    """Load a bounded set of ORM rows without an empty ``IN`` query.

    This deliberately tiny helper keeps the batching visible at each call
    site.  Production's 7,854 mappings exceed conservative SQLite bind-value
    limits, so each page stays safely below 999 while still avoiding an N+1
    query loop.
    """

    if not values:
        return []
    rows: list[object] = []
    for start in range(0, len(values), _ADOPTION_PAGE_SIZE):
        page = values[start : start + _ADOPTION_PAGE_SIZE]
        rows.extend(
            session.scalars(select(model).where(column.in_(page)))  # type: ignore[attr-defined,arg-type]
        )
    return rows


def _rows_by_id(
    session: Session,
    model: type[object],
    column: object,
    values: tuple[int, ...],
) -> dict[int, object]:
    """Index a bounded row fetch by its integer primary key."""

    return {
        row.id: row  # type: ignore[attr-defined]
        for row in _rows_for_values(session, model, column, values)
    }


def _insert_adoption_rows(
    session: Session,
    model: type[object],
    rows: list[dict[str, object]],
) -> None:
    """Insert high-cardinality adoption rows in bounded SQL statements.

    A 500-row page is deliberately boring: it is far below Postgres's
    parameter limit even for the widest provenance rows, works in the SQLite
    disposable-schema tests, and prevents a one-row ORM flush from becoming a
    multi-minute network loop.  ``session.execute`` still uses the caller's
    transaction; it does not commit or bypass rollback semantics.
    """

    for start in range(0, len(rows), _ADOPTION_PAGE_SIZE):
        page = rows[start : start + _ADOPTION_PAGE_SIZE]
        session.execute(insert(model).values(page))  # type: ignore[arg-type]


def _plan_identity_adoption(
    session: Session,
    *,
    blob_store: BlobStore,
    extractor_languages: Mapping[str, str] | None,
    default_language: str | None,
    valid_object_types: Iterable[str] | None,
) -> _IdentityAdoptionPlan:
    return _plan_identity_adoption_snapshot(
        _load_identity_adoption_snapshot(session),
        blob_store=blob_store,
        extractor_languages=extractor_languages,
        default_language=default_language,
        valid_object_types=valid_object_types,
    )


def _load_identity_adoption_snapshot(session: Session) -> _IdentityAdoptionSnapshot:
    """Load every database fact before the planner touches slow blob storage.

    This is intentionally a finite set of small relational rows.  The raw
    document body is left in blob storage and is resolved later, once per
    document, by _plan_identity_adoption_snapshot.
    """

    documents = tuple(
        _AdoptionDocument(
            id=row.id,
            text=row.text,
            text_blob_key=row.text_blob_key,
            text_sha256=row.text_sha256,
            text_length=row.text_length,
        )
        for row in session.scalars(select(DocumentORM).order_by(DocumentORM.id))
    )
    document_identity_ids = frozenset(
        session.scalars(select(DocumentIdentityORM.document_id))
    )
    mentions = tuple(
        _AdoptionMention(
            id=row.id,
            document_id=row.document_id,
            text=row.text,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            object_type=row.object_type,
            extractor_version_id=row.extractor_version_id,
        )
        for row in session.scalars(select(MentionORM).order_by(MentionORM.id))
    )
    mapped_mention_ids = frozenset(
        session.scalars(select(MentionEvidenceIdentityORM.mention_id))
    )
    extractor_names_by_id = {
        row.id: row.name for row in session.scalars(select(ExtractorVersionORM))
    }
    decisions = tuple(
        _AdoptionDecision(
            id=row.id,
            left_mention_id=row.left_mention_id,
            right_mention_id=row.right_mention_id,
        )
        for row in session.scalars(select(ResolutionDecisionORM).order_by(ResolutionDecisionORM.id))
    )
    constrained_decision_ids = frozenset(
        session.scalars(select(ResolutionConstraintORM.source_decision_id))
    )
    remap_status_counts: dict[str, int] = defaultdict(int)
    for remap in remap_resolution_constraints(session):
        remap_status_counts[remap.status] += 1

    return _IdentityAdoptionSnapshot(
        documents=documents,
        document_identity_ids=document_identity_ids,
        mentions=mentions,
        mapped_mention_ids=mapped_mention_ids,
        extractor_names_by_id=extractor_names_by_id,
        decisions=decisions,
        constrained_decision_ids=constrained_decision_ids,
        remap_status_counts=dict(remap_status_counts),
    )


def _plan_identity_adoption_snapshot(
    snapshot: _IdentityAdoptionSnapshot,
    *,
    blob_store: BlobStore,
    extractor_languages: Mapping[str, str] | None,
    default_language: str | None,
    valid_object_types: Iterable[str] | None,
) -> _IdentityAdoptionPlan:
    normalized_languages = {
        name: canonical_language(language)
        for name, language in (extractor_languages or {}).items()
    }
    normalized_default = canonical_language(default_language) if default_language is not None else None
    allowed_types = frozenset(valid_object_types) if valid_object_types is not None else None

    documents = snapshot.documents
    document_by_id = {row.id: row for row in documents}
    document_identity_ids = snapshot.document_identity_ids
    missing_document_ids = tuple(
        row.id for row in documents if row.id not in document_identity_ids
    )

    mentions = snapshot.mentions
    mapped_mention_ids = snapshot.mapped_mention_ids
    extractor_names_by_id = snapshot.extractor_names_by_id

    # Static checks remain per mention.  The deferred source checks below are
    # grouped by document so repeated legacy mentions never trigger repeated
    # remote blob reads.  The only raw text held at once is the current
    # document's text, which bounds the planner's text memory independent of
    # corpus size.
    mention_results: dict[int, _AdoptionMentionInput | IdentityAdoptionIssue] = {}
    pending_by_document: dict[int, list[_PendingAdoptionMention]] = {}
    for mention in mentions:
        if mention.id in mapped_mention_ids:
            continue
        document = document_by_id.get(mention.document_id)
        if document is None:
            mention_results[mention.id] = IdentityAdoptionIssue(
                "missing_document",
                mention.id,
                f"mention references absent document {mention.document_id}",
            )
            continue
        if allowed_types is not None and mention.object_type not in allowed_types:
            mention_results[mention.id] = IdentityAdoptionIssue(
                "invalid_object_type",
                mention.id,
                f"object_type {mention.object_type!r} is not declared by the supplied ontology",
            )
            continue

        extractor_name = extractor_names_by_id.get(mention.extractor_version_id)
        if extractor_name is None:
            mention_results[mention.id] = IdentityAdoptionIssue(
                "missing_extractor_version",
                mention.id,
                f"extractor version {mention.extractor_version_id} is absent",
            )
            continue
        language = normalized_languages.get(extractor_name, normalized_default)
        if language is None:
            mention_results[mention.id] = IdentityAdoptionIssue(
                "missing_language",
                mention.id,
                f"no language mapping for extractor {extractor_name!r}; "
                "pass --extractor-language or --default-language",
            )
            continue
        pending_by_document.setdefault(document.id, []).append(
            _PendingAdoptionMention(
                mention=mention,
                document=document,
                language=language,
            )
        )

    for pending_mentions in pending_by_document.values():
        document = pending_mentions[0].document
        try:
            # ``resolve_document_text`` only reads the five scalar attributes
            # represented by _AdoptionDocument, so the detached snapshot keeps
            # its existing blob/hash validation behavior exactly.
            text = resolve_document_text(document, blob_store)  # type: ignore[arg-type]
            actual_hash = sha256_text(text)
            if document.text_sha256 is not None and document.text_sha256 != actual_hash:
                raise IdentityInvariantError(
                    f"document hash is {document.text_sha256}, source text hashes to {actual_hash}"
                )
            if document.text_length is not None and document.text_length != len(text):
                raise IdentityInvariantError(
                    f"document text length is {document.text_length}, source text length is {len(text)}"
                )
        except (IdentityInvariantError, KeyError, ValueError) as exc:
            for pending in pending_mentions:
                mention_results[pending.mention.id] = IdentityAdoptionIssue(
                    "invalid_source_span",
                    pending.mention.id,
                    str(exc),
                )
            continue

        for pending in pending_mentions:
            mention = pending.mention
            try:
                if not (0 <= mention.start_offset < mention.end_offset <= len(text)):
                    raise IdentityInvariantError("mention offsets are outside original source text")
                if text[mention.start_offset : mention.end_offset] != mention.text:
                    raise IdentityInvariantError(
                        "mention text does not match original source offsets"
                    )
            except IdentityInvariantError as exc:
                mention_results[mention.id] = IdentityAdoptionIssue(
                    "invalid_source_span",
                    mention.id,
                    str(exc),
                )
                continue
            mention_results[mention.id] = _AdoptionMentionInput(
                mention_id=mention.id,
                document_id=document.id,
                source_text_sha256=actual_hash,
                language=pending.language,
            )

    # The old loop emitted mention issues in mention-id order even when the
    # blobs happened to be shared.  Reconstruct that order after grouping so
    # the JSON audit remains stable for operators and tests.
    issues: list[IdentityAdoptionIssue] = []
    mention_inputs: list[_AdoptionMentionInput] = []
    for mention in mentions:
        result = mention_results.get(mention.id)
        if result is None:
            continue
        if isinstance(result, IdentityAdoptionIssue):
            issues.append(result)
        else:
            mention_inputs.append(result)

    decisions = snapshot.decisions
    constrained_decision_ids = snapshot.constrained_decision_ids
    mappable_mention_ids = mapped_mention_ids | {item.mention_id for item in mention_inputs}
    missing_decision_ids: list[int] = []
    for decision in decisions:
        if decision.id in constrained_decision_ids:
            continue
        missing_endpoints = [
            mention_id
            for mention_id in (decision.left_mention_id, decision.right_mention_id)
            if mention_id not in mappable_mention_ids
        ]
        if missing_endpoints:
            issues.append(
                IdentityAdoptionIssue(
                    "decision_missing_evidence",
                    decision.id,
                    "decision endpoint(s) have no valid evidence mapping: "
                    + ", ".join(str(mention_id) for mention_id in missing_endpoints),
                )
            )
            continue
        missing_decision_ids.append(decision.id)

    report = IdentityAdoptionReport(
        documents_total=len(documents),
        document_identities_existing=len(document_identity_ids),
        document_identities_missing=len(missing_document_ids),
        mentions_total=len(mentions),
        mention_mappings_existing=len(mapped_mention_ids),
        mention_mappings_missing=len(mentions) - len(mapped_mention_ids),
        decisions_total=len(decisions),
        constraints_existing=len(constrained_decision_ids),
        constraints_missing=len(decisions) - len(constrained_decision_ids),
        errors=tuple(issues),
        remap_status_counts=dict(snapshot.remap_status_counts),
    )
    return _IdentityAdoptionPlan(
        report=report,
        document_ids=missing_document_ids,
        mention_inputs=tuple(mention_inputs),
        decision_ids=tuple(missing_decision_ids),
    )


def _evidence_for_mention(session: Session, mention_id: int) -> EvidenceIdentityORM:
    mapping = session.get(MentionEvidenceIdentityORM, mention_id)
    if mapping is None:
        raise IdentityInvariantError(
            f"mention {mention_id} has no durable evidence mapping; run identity adoption first"
        )
    evidence = session.get(EvidenceIdentityORM, mapping.evidence_identity_id)
    if evidence is None:
        raise IdentityInvariantError(
            f"mention {mention_id} maps to missing evidence {mapping.evidence_identity_id}"
        )
    return evidence


def _assert_evidence_matches(
    evidence: EvidenceIdentityORM,
    *,
    document_identity: DocumentIdentityORM,
    fingerprint: str,
    source_text_sha256: str,
    mention: MentionORM,
    language: str,
) -> None:
    actual = (
        evidence.document_identity_id,
        evidence.fingerprint,
        evidence.identity_version,
        evidence.source_text_sha256,
        evidence.start_offset,
        evidence.end_offset,
        evidence.object_type,
        evidence.language,
    )
    expected = (
        document_identity.id,
        fingerprint,
        EVIDENCE_IDENTITY_VERSION,
        source_text_sha256,
        mention.start_offset,
        mention.end_offset,
        mention.object_type,
        language,
    )
    if actual != expected:
        raise IdentityInvariantError(
            f"evidence identity {evidence.id} does not match mention {mention.id}'s durable signature"
        )


def _canonical_document_uid(document_uid: str) -> str:
    try:
        return str(uuid.UUID(document_uid))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IdentityInvariantError("document_uid must be a UUID string") from exc


def _canonical_sha256(value: str) -> str:
    normalized = value.lower()
    if _SHA256_RE.fullmatch(normalized):
        return normalized
    raise IdentityInvariantError("source_text_sha256 must be a SHA-256 hex digest")
