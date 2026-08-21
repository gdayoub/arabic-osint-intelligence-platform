"""Immutable release candidates and compare-and-publish coordination.

Candidate files are uploaded under SHA-256-derived keys and the manifest is
written last.  Nothing becomes current merely because it exists in blob
storage.  Promotion first records a durable pending plan, then an external
adapter publishes it, and finally a verified live observation advances the
singleton publication state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Callable, Iterable, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ops.events import PipelineEventType, PipelineReasonCode
from src.ops.health import project_run_health
from src.ops.ledger import append_pipeline_event, load_run_events
from src.store.blob import BlobStore
from src.store.orm import PipelineEventORM, PublicationStateORM


RELEASE_MANIFEST_SCHEMA_VERSION = "1.0.0"
RELEASE_BLOB_PREFIX = "v1/releases"
PUBLICATION_STATE_ID = 1


class ReleaseError(RuntimeError):
    """Base class for release construction and publication failures."""


class CandidateIntegrityError(ReleaseError):
    """Raised when a candidate does not match its immutable manifest."""


class CandidateRunNotEligible(ReleaseError):
    """Raised when a candidate is not backed by one healthy completed run."""


class StaleReleaseCandidate(ReleaseError):
    """Raised before deployment when a normal candidate is not newer."""


class ReleaseAlreadyCurrent(ReleaseError):
    """Internal compare result used to make an exact retry idempotent."""


class PromotionInProgress(ReleaseError):
    """Raised while another promotion still needs reconciliation."""


class PromotionUncertain(ReleaseError):
    """Raised when deployment may have happened and must be reconciled."""


class ReleasePublishFailed(ReleaseError):
    """Raised when the adapter can prove the candidate is not live."""


class RollbackNotAllowed(ReleaseError):
    """Raised when a requested rollback is not an identified old release."""


class DeploymentStatus(StrEnum):
    """What the deployment adapter can prove about one promotion."""

    LIVE = "live"
    NOT_LIVE = "not_live"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReleaseArtifact:
    """One named byte payload and the HTTP media type it must be served with."""

    path: str
    data: bytes
    content_type: str


@dataclass(frozen=True)
class ArtifactDescriptor:
    path: str
    blob_key: str
    sha256: str
    byte_length: int
    content_type: str


@dataclass(frozen=True)
class ReleaseCandidate:
    release_id: str
    run_id: str
    commit_sha: str
    created_at: datetime
    data_sequence: int
    artifacts: tuple[ArtifactDescriptor, ...]
    manifest_key: str
    manifest_sha256: str


@dataclass(frozen=True)
class PromotionPlan:
    candidate: ReleaseCandidate
    run_id: str
    commit_sha: str
    promotion_sequence: int
    rollback_of_promotion_sequence: int | None


@dataclass(frozen=True)
class PublicationSnapshot:
    current_release_id: str | None
    current_manifest_key: str | None
    current_manifest_sha256: str | None
    current_data_sequence: int | None
    max_data_sequence_seen: int
    promotion_sequence: int
    pending_release_id: str | None
    pending_promotion_sequence: int | None


class ReleasePublisher(Protocol):
    """Deployment boundary; production wiring can target Cloudflare later."""

    def publish(
        self,
        plan: PromotionPlan,
        artifacts: tuple[ReleaseArtifact, ...],
    ) -> None: ...

    def status(self, plan: PromotionPlan) -> DeploymentStatus: ...


SessionFactory = Callable[[], Session]


_RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RUN_TERMINAL_EVENT_TYPES = frozenset(
    {
        PipelineEventType.RUN_SUCCEEDED,
        PipelineEventType.RUN_FAILED,
        PipelineEventType.RUN_ABANDONED,
    }
)
_RUN_TERMINAL_EVENT_VALUES = frozenset(
    item.value for item in _RUN_TERMINAL_EVENT_TYPES
)
_DATA_BOUNDARY_EVENT_VALUES = frozenset(
    {
        PipelineEventType.STAGE_STARTED.value,
        PipelineEventType.STAGE_SUCCEEDED.value,
        PipelineEventType.STAGE_FAILED.value,
        PipelineEventType.SOURCE_STARTED.value,
        PipelineEventType.SOURCE_SUCCEEDED.value,
        PipelineEventType.SOURCE_FAILED.value,
    }
)
_CANDIDATE_PREPARATION_STAGE = "prepare_release"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _database_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata even for timezone-aware columns."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _database_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CandidateIntegrityError("manifest created_at must be an ISO timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CandidateIntegrityError("manifest created_at is not valid ISO time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateIntegrityError("manifest created_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: dict[str, Any]) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (encoded + "\n").encode("utf-8")


def _strict_json_object(data: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CandidateIntegrityError(f"manifest repeats key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateIntegrityError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CandidateIntegrityError("manifest root must be an object")
    return value


def _validate_release_id(value: str) -> str:
    if not isinstance(value, str) or _RELEASE_ID_PATTERN.fullmatch(value) is None:
        raise CandidateIntegrityError(
            "release_id must use 1-100 ASCII letters, digits, dots, underscores, or dashes"
        )
    return value


def _validate_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise CandidateIntegrityError(f"{field} must be a lower-case SHA-256 digest")
    return value


def _validate_positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CandidateIntegrityError(f"{field} must be a positive integer")
    return value


def _validate_artifact_path(path: Any) -> str:
    if not isinstance(path, str) or not path or len(path) > 240:
        raise CandidateIntegrityError("artifact path must contain 1-240 characters")
    if "\\" in path or any(ord(character) < 32 for character in path):
        raise CandidateIntegrityError(f"artifact path is not safe: {path!r}")
    parsed = PurePosixPath(path)
    if (
        not parsed.parts
        or parsed.is_absolute()
        or path != parsed.as_posix()
        or ".." in parsed.parts
    ):
        raise CandidateIntegrityError(f"artifact path is not safe: {path!r}")
    if any(part in {"", "."} for part in parsed.parts):
        raise CandidateIntegrityError(f"artifact path is not canonical: {path!r}")
    return path


def _validate_content_type(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 120:
        raise CandidateIntegrityError("content_type must contain 1-120 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CandidateIntegrityError("content_type cannot contain control characters")
    return value


def artifact_blob_key(sha256: str, content_type: str) -> str:
    digest = _validate_sha256(sha256, field="artifact sha256")
    normalized_content_type = _validate_content_type(content_type)
    media_digest = _sha256(normalized_content_type.encode("utf-8"))[:16]
    return f"{RELEASE_BLOB_PREFIX}/artifacts/{digest[:2]}/{digest}-{media_digest}"


def manifest_blob_key(sha256: str) -> str:
    digest = _validate_sha256(sha256, field="manifest sha256")
    return f"{RELEASE_BLOB_PREFIX}/manifests/{digest[:2]}/{digest}.json"


def _put_verified(
    blob_store: BlobStore,
    *,
    key: str,
    data: bytes,
    content_type: str,
) -> None:
    if blob_store.exists(key):
        stored = blob_store.get(key)
    else:
        blob_store.put(key, data, content_type=content_type)
        stored = blob_store.get(key)
    if stored != data:
        raise CandidateIntegrityError(f"blob {key!r} failed write verification")


def _normalize_artifacts(
    artifacts: Iterable[ReleaseArtifact],
) -> tuple[ReleaseArtifact, ...]:
    normalized: list[ReleaseArtifact] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, ReleaseArtifact):
            raise CandidateIntegrityError("artifacts must be ReleaseArtifact values")
        path = _validate_artifact_path(artifact.path)
        if path in seen_paths:
            raise CandidateIntegrityError(f"artifact path is duplicated: {path!r}")
        if not isinstance(artifact.data, bytes):
            raise CandidateIntegrityError(f"artifact {path!r} data must be bytes")
        content_type = _validate_content_type(artifact.content_type)
        seen_paths.add(path)
        normalized.append(
            ReleaseArtifact(path=path, data=artifact.data, content_type=content_type)
        )
    if not normalized:
        raise CandidateIntegrityError("a release candidate needs at least one artifact")
    return tuple(sorted(normalized, key=lambda item: item.path))


def _descriptor_for(artifact: ReleaseArtifact) -> ArtifactDescriptor:
    digest = _sha256(artifact.data)
    return ArtifactDescriptor(
        path=artifact.path,
        blob_key=artifact_blob_key(digest, artifact.content_type),
        sha256=digest,
        byte_length=len(artifact.data),
        content_type=artifact.content_type,
    )


def _manifest_value(
    *,
    release_id: str,
    run_id: str,
    commit_sha: str,
    created_at: datetime,
    data_sequence: int,
    descriptors: tuple[ArtifactDescriptor, ...],
) -> dict[str, Any]:
    return {
        "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
        "release_id": release_id,
        "run_id": run_id,
        "commit_sha": commit_sha,
        "created_at": _isoformat(created_at),
        "data_sequence": data_sequence,
        "artifacts": [
            {
                "path": item.path,
                "blob_key": item.blob_key,
                "sha256": item.sha256,
                "byte_length": item.byte_length,
                "content_type": item.content_type,
            }
            for item in descriptors
        ],
    }


def create_release_candidate(
    session: Session,
    blob_store: BlobStore,
    *,
    run_id: str,
    commit_sha: str,
    artifacts: Iterable[ReleaseArtifact],
    release_id: str | None = None,
    occurred_at: datetime | None = None,
) -> ReleaseCandidate:
    """Upload a complete immutable candidate and register it last.

    The nested transaction means a failed artifact or manifest upload leaves
    no reservation/candidate event even when the caller catches the exception
    and commits other work.  Content-addressed orphan blobs are harmless and
    reusable by a retry; no manifest points at a partial upload.
    """
    normalized_artifacts = _normalize_artifacts(artifacts)
    release_id = _validate_release_id(release_id or f"release-{uuid4().hex}")

    with session.begin_nested():
        reservation = append_pipeline_event(
            session,
            event_key=f"{release_id}:reserved",
            run_id=run_id,
            release_id=release_id,
            event_type=PipelineEventType.RELEASE_RESERVED,
            commit_sha=commit_sha,
            occurred_at=occurred_at,
        )
        data_sequence = reservation.id
        created_at = _database_utc(reservation.occurred_at)

        descriptors = tuple(
            _descriptor_for(artifact) for artifact in normalized_artifacts
        )
        for artifact, descriptor in zip(
            normalized_artifacts,
            descriptors,
            strict=True,
        ):
            _put_verified(
                blob_store,
                key=descriptor.blob_key,
                data=artifact.data,
                content_type=artifact.content_type,
            )

        manifest_bytes = _canonical_json(
            _manifest_value(
                release_id=release_id,
                run_id=run_id,
                commit_sha=commit_sha,
                created_at=created_at,
                data_sequence=data_sequence,
                descriptors=descriptors,
            )
        )
        manifest_sha256 = _sha256(manifest_bytes)
        manifest_key = manifest_blob_key(manifest_sha256)
        _put_verified(
            blob_store,
            key=manifest_key,
            data=manifest_bytes,
            content_type="application/json; charset=utf-8",
        )

        append_pipeline_event(
            session,
            event_key=f"{release_id}:candidate-created",
            run_id=run_id,
            release_id=release_id,
            event_type=PipelineEventType.RELEASE_CANDIDATE_CREATED,
            commit_sha=commit_sha,
            occurred_at=created_at,
            data_sequence=data_sequence,
            manifest_sha256=manifest_sha256,
        )

    return ReleaseCandidate(
        release_id=release_id,
        run_id=run_id,
        commit_sha=commit_sha,
        created_at=created_at,
        data_sequence=data_sequence,
        artifacts=descriptors,
        manifest_key=manifest_key,
        manifest_sha256=manifest_sha256,
    )


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    *,
    location: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected.difference(actual))
        extra = sorted(actual.difference(expected))
        raise CandidateIntegrityError(
            f"{location} fields differ; missing={missing!r}, extra={extra!r}"
        )


def load_release_candidate(
    blob_store: BlobStore,
    *,
    manifest_key: str,
    expected_manifest_sha256: str | None = None,
) -> ReleaseCandidate:
    """Load and strictly validate a manifest, without yet fetching artifacts."""
    try:
        manifest_bytes = blob_store.get(manifest_key)
    except KeyError as exc:
        raise CandidateIntegrityError(f"manifest blob is missing: {manifest_key!r}") from exc

    actual_manifest_sha256 = _sha256(manifest_bytes)
    if expected_manifest_sha256 is not None:
        expected_manifest_sha256 = _validate_sha256(
            expected_manifest_sha256,
            field="expected manifest sha256",
        )
        if actual_manifest_sha256 != expected_manifest_sha256:
            raise CandidateIntegrityError("manifest SHA-256 does not match registration")
    if manifest_key != manifest_blob_key(actual_manifest_sha256):
        raise CandidateIntegrityError("manifest key is not content-addressed by its bytes")

    value = _strict_json_object(manifest_bytes)
    _require_exact_keys(
        value,
        {
            "schema_version",
            "release_id",
            "run_id",
            "commit_sha",
            "created_at",
            "data_sequence",
            "artifacts",
        },
        location="manifest",
    )
    if value["schema_version"] != RELEASE_MANIFEST_SCHEMA_VERSION:
        raise CandidateIntegrityError("manifest schema_version is unsupported")

    release_id = _validate_release_id(value["release_id"])
    run_id = value["run_id"]
    commit_sha = value["commit_sha"]
    if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
        raise CandidateIntegrityError("manifest run_id is invalid")
    if not isinstance(commit_sha, str) or not commit_sha or len(commit_sha) > 64:
        raise CandidateIntegrityError("manifest commit_sha is invalid")
    data_sequence = _validate_positive_integer(
        value["data_sequence"],
        field="manifest data_sequence",
    )
    created_at = _parse_datetime(value["created_at"])

    raw_artifacts = value["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise CandidateIntegrityError("manifest artifacts must be a non-empty list")
    descriptors: list[ArtifactDescriptor] = []
    seen_paths: set[str] = set()
    for raw_artifact in raw_artifacts:
        if not isinstance(raw_artifact, dict):
            raise CandidateIntegrityError("manifest artifact must be an object")
        _require_exact_keys(
            raw_artifact,
            {"path", "blob_key", "sha256", "byte_length", "content_type"},
            location="manifest artifact",
        )
        path = _validate_artifact_path(raw_artifact["path"])
        if path in seen_paths:
            raise CandidateIntegrityError(f"manifest repeats artifact path {path!r}")
        seen_paths.add(path)
        digest = _validate_sha256(raw_artifact["sha256"], field="artifact sha256")
        content_type = _validate_content_type(raw_artifact["content_type"])
        blob_key = raw_artifact["blob_key"]
        if blob_key != artifact_blob_key(digest, content_type):
            raise CandidateIntegrityError(
                f"artifact {path!r} key is not content-addressed by its hash"
            )
        byte_length = raw_artifact["byte_length"]
        if isinstance(byte_length, bool) or not isinstance(byte_length, int):
            raise CandidateIntegrityError("artifact byte_length must be an integer")
        if byte_length < 0:
            raise CandidateIntegrityError("artifact byte_length cannot be negative")
        descriptors.append(
            ArtifactDescriptor(
                path=path,
                blob_key=blob_key,
                sha256=digest,
                byte_length=byte_length,
                content_type=content_type,
            )
        )

    if [item.path for item in descriptors] != sorted(item.path for item in descriptors):
        raise CandidateIntegrityError("manifest artifacts must use canonical path order")

    return ReleaseCandidate(
        release_id=release_id,
        run_id=run_id,
        commit_sha=commit_sha,
        created_at=created_at,
        data_sequence=data_sequence,
        artifacts=tuple(descriptors),
        manifest_key=manifest_key,
        manifest_sha256=actual_manifest_sha256,
    )


def materialize_release_candidate(
    blob_store: BlobStore,
    candidate: ReleaseCandidate,
) -> tuple[ReleaseArtifact, ...]:
    """Fetch every artifact and prove its byte length and digest."""
    materialized: list[ReleaseArtifact] = []
    for descriptor in candidate.artifacts:
        try:
            data = blob_store.get(descriptor.blob_key)
        except KeyError as exc:
            raise CandidateIntegrityError(
                f"artifact blob is missing: {descriptor.path!r}"
            ) from exc
        if len(data) != descriptor.byte_length:
            raise CandidateIntegrityError(
                f"artifact {descriptor.path!r} byte length does not match manifest"
            )
        if _sha256(data) != descriptor.sha256:
            raise CandidateIntegrityError(
                f"artifact {descriptor.path!r} SHA-256 does not match manifest"
            )
        materialized.append(
            ReleaseArtifact(
                path=descriptor.path,
                data=data,
                content_type=descriptor.content_type,
            )
        )
    return tuple(materialized)


def _publication_state(session: Session, *, lock: bool) -> PublicationStateORM:
    statement = select(PublicationStateORM).where(
        PublicationStateORM.id == PUBLICATION_STATE_ID
    )
    if lock:
        statement = statement.with_for_update()
    state = session.scalar(statement)
    if state is None:
        state = PublicationStateORM(
            id=PUBLICATION_STATE_ID,
            max_data_sequence_seen=0,
            promotion_sequence=0,
            updated_at=_utc_now(),
        )
        session.add(state)
        session.flush()
    return state


def publication_snapshot(session: Session) -> PublicationSnapshot:
    state = _publication_state(session, lock=False)
    return PublicationSnapshot(
        current_release_id=state.current_release_id,
        current_manifest_key=state.current_manifest_key,
        current_manifest_sha256=state.current_manifest_sha256,
        current_data_sequence=state.current_data_sequence,
        max_data_sequence_seen=state.max_data_sequence_seen,
        promotion_sequence=state.promotion_sequence,
        pending_release_id=state.pending_release_id,
        pending_promotion_sequence=state.pending_promotion_sequence,
    )


def _candidate_registration(
    session: Session,
    candidate: ReleaseCandidate,
) -> PipelineEventORM:
    row = session.scalar(
        select(PipelineEventORM).where(
            PipelineEventORM.release_id == candidate.release_id,
            PipelineEventORM.event_type
            == PipelineEventType.RELEASE_CANDIDATE_CREATED.value,
        )
    )
    if row is None:
        raise CandidateIntegrityError("release candidate is not registered in the ledger")
    if (
        row.data_sequence != candidate.data_sequence
        or row.manifest_sha256 != candidate.manifest_sha256
    ):
        raise CandidateIntegrityError("candidate manifest differs from ledger registration")
    if row.run_id != candidate.run_id or row.commit_sha != candidate.commit_sha:
        raise CandidateIntegrityError("candidate identity differs from ledger registration")
    return row


def _event_order(row: PipelineEventORM) -> tuple[datetime, int]:
    """Match the immutable ledger's chronological ordering for one event."""
    return (_database_utc(row.occurred_at), row.id)


def _require_eligible_candidate_run(
    session: Session,
    candidate: ReleaseCandidate,
    registration: PipelineEventORM,
) -> None:
    """Fail closed unless the candidate's own run completed healthily.

    Promotion can use a separate operator run ID (notably for a rollback),
    but it must never borrow that run's health.  The candidate's recorded run
    is the only evidence that its immutable bytes came from a finished data
    build.
    """
    events = load_run_events(session, candidate.run_id)
    started = next(
        (
            row
            for row in events
            if row.event_type == PipelineEventType.RUN_STARTED.value
        ),
        None,
    )
    if started is None:
        raise CandidateRunNotEligible(
            "candidate run has no run_started event and cannot be promoted"
        )
    if started.commit_sha != candidate.commit_sha:
        raise CandidateRunNotEligible(
            "candidate commit does not match the run that produced it"
        )

    terminals = [row for row in events if row.event_type in _RUN_TERMINAL_EVENT_VALUES]
    if len(terminals) != 1:
        raise CandidateRunNotEligible(
            "candidate run must have exactly one terminal event before promotion"
        )
    terminal = terminals[0]
    if terminal.event_type != PipelineEventType.RUN_SUCCEEDED.value:
        raise CandidateRunNotEligible(
            "candidate run did not complete successfully and cannot be promoted"
        )
    if terminal.commit_sha != candidate.commit_sha:
        raise CandidateRunNotEligible(
            "candidate commit does not match its successful terminal event"
        )

    # Candidate registration is deliberately between run start and terminal
    # success.  A later registration is a new, unobserved bundle, not proof
    # that the completed run produced the candidate it is asking to promote.
    if _event_order(registration) <= _event_order(started):
        raise CandidateRunNotEligible(
            "candidate registration must follow the run_started event"
        )
    if _event_order(registration) >= _event_order(terminal):
        raise CandidateRunNotEligible(
            "candidate registration must precede the successful terminal event"
        )

    later_data_boundaries = [
        row
        for row in events
        if row.event_type in _DATA_BOUNDARY_EVENT_VALUES
        and row.stage != _CANDIDATE_PREPARATION_STAGE
        and _event_order(row) > _event_order(registration)
    ]
    if later_data_boundaries:
        raise CandidateRunNotEligible(
            "candidate registration precedes later data-work boundaries"
        )

    preparation_events = [
        row for row in events if row.stage == _CANDIDATE_PREPARATION_STAGE
    ]
    if preparation_events:
        preparation_started = next(
            (
                row
                for row in preparation_events
                if row.event_type == PipelineEventType.STAGE_STARTED.value
            ),
            None,
        )
        preparation_succeeded = next(
            (
                row
                for row in preparation_events
                if row.event_type == PipelineEventType.STAGE_SUCCEEDED.value
            ),
            None,
        )
        if (
            preparation_started is None
            or preparation_succeeded is None
            or _event_order(preparation_started) >= _event_order(registration)
            or _event_order(registration) >= _event_order(preparation_succeeded)
        ):
            raise CandidateRunNotEligible(
                "candidate registration must be inside successful prepare_release"
            )

    try:
        health = project_run_health(events)
    except ValueError as exc:
        raise CandidateRunNotEligible(
            "candidate run history cannot prove healthy completion"
        ) from exc
    if health.status != "healthy":
        raise CandidateRunNotEligible(
            "candidate run must project healthy before promotion"
        )


def _clear_pending(state: PublicationStateORM) -> None:
    state.pending_release_id = None
    state.pending_run_id = None
    state.pending_commit_sha = None
    state.pending_manifest_key = None
    state.pending_manifest_sha256 = None
    state.pending_data_sequence = None
    state.pending_promotion_sequence = None
    state.pending_rollback_of_promotion_sequence = None


def begin_promotion(
    session: Session,
    candidate: ReleaseCandidate,
    *,
    run_id: str | None = None,
    commit_sha: str | None = None,
    rollback_of_promotion_sequence: int | None = None,
    operation_id: str | None = None,
    occurred_at: datetime | None = None,
) -> PromotionPlan:
    """Verify candidate-run eligibility, then persist a pre-deploy promotion plan."""
    registration = _candidate_registration(session, candidate)
    _require_eligible_candidate_run(session, candidate, registration)
    state = _publication_state(session, lock=True)
    if state.pending_release_id is not None:
        raise PromotionInProgress(
            f"promotion {state.pending_promotion_sequence} still needs reconciliation"
        )

    operation_run_id = run_id or candidate.run_id
    operation_commit_sha = commit_sha or candidate.commit_sha
    if rollback_of_promotion_sequence is None:
        if (
            state.current_release_id == candidate.release_id
            and state.current_manifest_sha256 == candidate.manifest_sha256
            and state.current_data_sequence == candidate.data_sequence
        ):
            raise ReleaseAlreadyCurrent(
                f"release {candidate.release_id!r} is already current"
            )
        if candidate.data_sequence <= state.max_data_sequence_seen:
            append_pipeline_event(
                session,
                event_key=(
                    f"{candidate.release_id}:superseded-at:"
                    f"{state.promotion_sequence}"
                ),
                run_id=candidate.run_id,
                release_id=candidate.release_id,
                event_type=PipelineEventType.RELEASE_SUPERSEDED,
                commit_sha=candidate.commit_sha,
                occurred_at=occurred_at,
                data_sequence=candidate.data_sequence,
                promotion_sequence=state.promotion_sequence,
                manifest_sha256=candidate.manifest_sha256,
            )
            raise StaleReleaseCandidate(
                f"candidate data sequence {candidate.data_sequence} is not above "
                f"high-water {state.max_data_sequence_seen}"
            )
    else:
        if state.promotion_sequence != rollback_of_promotion_sequence:
            raise RollbackNotAllowed(
                "the live promotion changed before rollback could begin"
            )
        if state.current_release_id is None:
            raise RollbackNotAllowed("there is no published release to roll back")
        if state.current_release_id == candidate.release_id:
            raise RollbackNotAllowed("rollback target is already the current release")
        prior_publication = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.release_id == candidate.release_id,
                PipelineEventORM.event_type
                == PipelineEventType.RELEASE_PUBLISHED.value,
                PipelineEventORM.data_sequence == candidate.data_sequence,
                PipelineEventORM.manifest_sha256 == candidate.manifest_sha256,
            )
        )
        if prior_publication is None:
            raise RollbackNotAllowed(
                "rollback target has never been successfully published"
            )

    operation_id = operation_id or uuid4().hex
    started = append_pipeline_event(
        session,
        event_key=f"promotion:{operation_id}:started",
        run_id=operation_run_id,
        release_id=candidate.release_id,
        event_type=PipelineEventType.PROMOTION_STARTED,
        commit_sha=operation_commit_sha,
        occurred_at=occurred_at,
        data_sequence=candidate.data_sequence,
        manifest_sha256=candidate.manifest_sha256,
        rollback_of_promotion_sequence=rollback_of_promotion_sequence,
    )
    promotion_sequence = started.id

    state.pending_release_id = candidate.release_id
    state.pending_run_id = operation_run_id
    state.pending_commit_sha = operation_commit_sha
    state.pending_manifest_key = candidate.manifest_key
    state.pending_manifest_sha256 = candidate.manifest_sha256
    state.pending_data_sequence = candidate.data_sequence
    state.pending_promotion_sequence = promotion_sequence
    state.pending_rollback_of_promotion_sequence = rollback_of_promotion_sequence
    state.updated_at = _database_utc(started.occurred_at)
    session.flush()

    return PromotionPlan(
        candidate=candidate,
        run_id=operation_run_id,
        commit_sha=operation_commit_sha,
        promotion_sequence=promotion_sequence,
        rollback_of_promotion_sequence=rollback_of_promotion_sequence,
    )


def _assert_pending_plan(
    state: PublicationStateORM,
    plan: PromotionPlan,
) -> None:
    expected = (
        plan.candidate.release_id,
        plan.run_id,
        plan.commit_sha,
        plan.candidate.manifest_key,
        plan.candidate.manifest_sha256,
        plan.candidate.data_sequence,
        plan.promotion_sequence,
        plan.rollback_of_promotion_sequence,
    )
    actual = (
        state.pending_release_id,
        state.pending_run_id,
        state.pending_commit_sha,
        state.pending_manifest_key,
        state.pending_manifest_sha256,
        state.pending_data_sequence,
        state.pending_promotion_sequence,
        state.pending_rollback_of_promotion_sequence,
    )
    if actual != expected:
        raise PromotionInProgress("publication state no longer matches this promotion")


def complete_promotion(
    session: Session,
    plan: PromotionPlan,
    *,
    occurred_at: datetime | None = None,
) -> PublicationSnapshot:
    """Record a verified live deployment and advance both monotonic marks."""
    state = _publication_state(session, lock=True)
    if (
        state.pending_release_id is None
        and state.promotion_sequence == plan.promotion_sequence
        and state.current_release_id == plan.candidate.release_id
        and state.current_manifest_sha256 == plan.candidate.manifest_sha256
    ):
        return publication_snapshot(session)
    _assert_pending_plan(state, plan)
    published = append_pipeline_event(
        session,
        event_key=f"promotion:{plan.promotion_sequence}:published",
        run_id=plan.run_id,
        release_id=plan.candidate.release_id,
        event_type=PipelineEventType.RELEASE_PUBLISHED,
        commit_sha=plan.commit_sha,
        occurred_at=occurred_at,
        data_sequence=plan.candidate.data_sequence,
        promotion_sequence=plan.promotion_sequence,
        manifest_sha256=plan.candidate.manifest_sha256,
        rollback_of_promotion_sequence=plan.rollback_of_promotion_sequence,
    )

    state.current_release_id = plan.candidate.release_id
    state.current_manifest_key = plan.candidate.manifest_key
    state.current_manifest_sha256 = plan.candidate.manifest_sha256
    state.current_data_sequence = plan.candidate.data_sequence
    state.max_data_sequence_seen = max(
        state.max_data_sequence_seen,
        plan.candidate.data_sequence,
    )
    state.promotion_sequence = plan.promotion_sequence
    _clear_pending(state)
    state.updated_at = _database_utc(published.occurred_at)
    session.flush()
    return publication_snapshot(session)


def fail_promotion(
    session: Session,
    plan: PromotionPlan,
    *,
    reason_code: PipelineReasonCode = PipelineReasonCode.RELEASE_PUBLISH_FAILED,
    occurred_at: datetime | None = None,
) -> PublicationSnapshot:
    """Record a definitely-not-live promotion while preserving current state."""
    if reason_code not in {
        PipelineReasonCode.RELEASE_CONTRACT_FAILED,
        PipelineReasonCode.RELEASE_PUBLISH_FAILED,
    }:
        raise ValueError("promotion failure requires a release failure reason code")
    state = _publication_state(session, lock=True)
    if state.pending_release_id is None:
        existing_failure = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.promotion_sequence == plan.promotion_sequence,
                PipelineEventORM.event_type == PipelineEventType.RELEASE_FAILED.value,
            )
        )
        if existing_failure is not None:
            return publication_snapshot(session)
    _assert_pending_plan(state, plan)
    failed = append_pipeline_event(
        session,
        event_key=f"promotion:{plan.promotion_sequence}:failed",
        run_id=plan.run_id,
        release_id=plan.candidate.release_id,
        event_type=PipelineEventType.RELEASE_FAILED,
        commit_sha=plan.commit_sha,
        occurred_at=occurred_at,
        reason_code=reason_code,
        data_sequence=plan.candidate.data_sequence,
        promotion_sequence=plan.promotion_sequence,
        manifest_sha256=plan.candidate.manifest_sha256,
        rollback_of_promotion_sequence=plan.rollback_of_promotion_sequence,
    )
    _clear_pending(state)
    state.updated_at = _database_utc(failed.occurred_at)
    session.flush()
    return publication_snapshot(session)


def _load_pending_plan(
    session: Session,
    blob_store: BlobStore,
) -> PromotionPlan | None:
    state = _publication_state(session, lock=False)
    if state.pending_release_id is None:
        return None
    if (
        state.pending_run_id is None
        or state.pending_commit_sha is None
        or state.pending_manifest_key is None
        or state.pending_manifest_sha256 is None
        or state.pending_data_sequence is None
        or state.pending_promotion_sequence is None
    ):
        raise CandidateIntegrityError("pending publication state is incomplete")
    candidate = load_release_candidate(
        blob_store,
        manifest_key=state.pending_manifest_key,
        expected_manifest_sha256=state.pending_manifest_sha256,
    )
    if (
        candidate.release_id != state.pending_release_id
        or candidate.data_sequence != state.pending_data_sequence
    ):
        raise CandidateIntegrityError("pending state does not match its manifest")
    return PromotionPlan(
        candidate=candidate,
        run_id=state.pending_run_id,
        commit_sha=state.pending_commit_sha,
        promotion_sequence=state.pending_promotion_sequence,
        rollback_of_promotion_sequence=(
            state.pending_rollback_of_promotion_sequence
        ),
    )


def _session(factory: SessionFactory) -> Session:
    session = factory()
    if not isinstance(session, Session):
        raise TypeError("session_factory must return a SQLAlchemy Session")
    return session


def _deployment_status(
    publisher: ReleasePublisher,
    plan: PromotionPlan,
) -> DeploymentStatus:
    try:
        return DeploymentStatus(publisher.status(plan))
    except Exception as exc:
        raise PromotionUncertain(
            f"promotion {plan.promotion_sequence} needs reconciliation"
        ) from exc


def promote_release(
    session_factory: SessionFactory,
    blob_store: BlobStore,
    publisher: ReleasePublisher,
    candidate: ReleaseCandidate,
    *,
    run_id: str | None = None,
    commit_sha: str | None = None,
    rollback_of_promotion_sequence: int | None = None,
    operation_id: str | None = None,
) -> PublicationSnapshot:
    """Publish one candidate with durable before/after deployment boundaries."""
    verified_candidate = load_release_candidate(
        blob_store,
        manifest_key=candidate.manifest_key,
        expected_manifest_sha256=candidate.manifest_sha256,
    )
    artifacts = materialize_release_candidate(blob_store, verified_candidate)

    session = _session(session_factory)
    try:
        try:
            plan = begin_promotion(
                session,
                verified_candidate,
                run_id=run_id,
                commit_sha=commit_sha,
                rollback_of_promotion_sequence=rollback_of_promotion_sequence,
                operation_id=operation_id,
            )
        except ReleaseAlreadyCurrent:
            snapshot = publication_snapshot(session)
            session.commit()
            return snapshot
        except StaleReleaseCandidate:
            # Supersession is a durable audit event, not a failed transaction.
            session.commit()
            raise
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    try:
        publisher.publish(plan, artifacts)
    except Exception as exc:
        # The external call may have succeeded before its connection failed.
        # Keep the pending row so a later monitor can ask the adapter what is
        # actually live instead of guessing and publishing over it.
        raise PromotionUncertain(
            f"promotion {plan.promotion_sequence} needs reconciliation"
        ) from exc

    status = _deployment_status(publisher, plan)
    if status == DeploymentStatus.UNKNOWN:
        raise PromotionUncertain(
            f"promotion {plan.promotion_sequence} needs reconciliation"
        )

    session = _session(session_factory)
    try:
        if status == DeploymentStatus.LIVE:
            snapshot = complete_promotion(session, plan)
            session.commit()
            return snapshot

        fail_promotion(session, plan)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    raise ReleasePublishFailed(
        f"promotion {plan.promotion_sequence} was verified as not live"
    )


def reconcile_pending_promotion(
    session_factory: SessionFactory,
    blob_store: BlobStore,
    publisher: ReleasePublisher,
) -> PublicationSnapshot | None:
    """Resolve the durable pending plan left by an interrupted publisher."""
    session = _session(session_factory)
    try:
        plan = _load_pending_plan(session, blob_store)
    finally:
        session.close()
    if plan is None:
        return None

    materialize_release_candidate(blob_store, plan.candidate)
    status = _deployment_status(publisher, plan)
    if status == DeploymentStatus.UNKNOWN:
        raise PromotionUncertain(
            f"promotion {plan.promotion_sequence} is still not observable"
        )

    session = _session(session_factory)
    try:
        if status == DeploymentStatus.LIVE:
            snapshot = complete_promotion(session, plan)
        else:
            snapshot = fail_promotion(session, plan)
        session.commit()
        return snapshot
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _registered_candidate_for_release(
    session: Session,
    blob_store: BlobStore,
    release_id: str,
) -> ReleaseCandidate:
    release_id = _validate_release_id(release_id)
    registration = session.scalar(
        select(PipelineEventORM).where(
            PipelineEventORM.release_id == release_id,
            PipelineEventORM.event_type
            == PipelineEventType.RELEASE_CANDIDATE_CREATED.value,
        )
    )
    if registration is None or registration.manifest_sha256 is None:
        raise RollbackNotAllowed("rollback target is not a registered candidate")
    candidate = load_release_candidate(
        blob_store,
        manifest_key=manifest_blob_key(registration.manifest_sha256),
        expected_manifest_sha256=registration.manifest_sha256,
    )
    if registration.data_sequence != candidate.data_sequence:
        raise CandidateIntegrityError("rollback target sequence differs from ledger")
    return candidate


def rollback_release(
    session_factory: SessionFactory,
    blob_store: BlobStore,
    publisher: ReleasePublisher,
    *,
    target_release_id: str,
    run_id: str,
    commit_sha: str,
    operation_id: str | None = None,
) -> PublicationSnapshot:
    """Promote an identified old payload without lowering the data high-water."""
    session = _session(session_factory)
    try:
        state = _publication_state(session, lock=False)
        if state.current_release_id is None or state.promotion_sequence <= 0:
            raise RollbackNotAllowed("there is no published release to roll back")
        rollback_of = state.promotion_sequence
        candidate = _registered_candidate_for_release(
            session,
            blob_store,
            target_release_id,
        )
    finally:
        session.close()

    return promote_release(
        session_factory,
        blob_store,
        publisher,
        candidate,
        run_id=run_id,
        commit_sha=commit_sha,
        rollback_of_promotion_sequence=rollback_of,
        operation_id=operation_id,
    )
