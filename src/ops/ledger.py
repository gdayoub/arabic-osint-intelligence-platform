"""Append-only writes for pipeline and release observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ops.events import PipelineEventType, PipelineReasonCode
from src.store.orm import PipelineEventORM


class EventValidationError(ValueError):
    """Raised before an invalid operational event reaches the database."""


class EventKeyConflict(RuntimeError):
    """Raised when one idempotency key is reused for different content."""


class InvalidEventTransition(RuntimeError):
    """Raised when an event contradicts the run history already recorded."""


_RUN_EVENTS = {
    PipelineEventType.RUN_STARTED,
    PipelineEventType.RUN_HEARTBEAT,
    PipelineEventType.RUN_SUCCEEDED,
    PipelineEventType.RUN_FAILED,
    PipelineEventType.RUN_ABANDONED,
}
_STAGE_EVENTS = {
    PipelineEventType.STAGE_STARTED,
    PipelineEventType.STAGE_SUCCEEDED,
    PipelineEventType.STAGE_FAILED,
}
_SOURCE_EVENTS = {
    PipelineEventType.SOURCE_STARTED,
    PipelineEventType.SOURCE_SUCCEEDED,
    PipelineEventType.SOURCE_FAILED,
}
_RELEASE_EVENTS = {
    PipelineEventType.RELEASE_RESERVED,
    PipelineEventType.RELEASE_CANDIDATE_CREATED,
    PipelineEventType.PROMOTION_STARTED,
    PipelineEventType.RELEASE_PUBLISHED,
    PipelineEventType.RELEASE_FAILED,
    PipelineEventType.RELEASE_SUPERSEDED,
}
_RUN_TERMINAL_EVENTS = {
    PipelineEventType.RUN_SUCCEEDED,
    PipelineEventType.RUN_FAILED,
    PipelineEventType.RUN_ABANDONED,
}
_STAGE_TERMINAL_EVENTS = {
    PipelineEventType.STAGE_SUCCEEDED,
    PipelineEventType.STAGE_FAILED,
}
_SOURCE_TERMINAL_EVENTS = {
    PipelineEventType.SOURCE_SUCCEEDED,
    PipelineEventType.SOURCE_FAILED,
}
_FAILURE_EVENTS = {
    PipelineEventType.RUN_FAILED,
    PipelineEventType.RUN_ABANDONED,
    PipelineEventType.STAGE_FAILED,
    PipelineEventType.SOURCE_FAILED,
    PipelineEventType.RELEASE_FAILED,
}
_SOURCE_WARNING_REASONS = {
    PipelineReasonCode.SOURCE_FETCH_FAILED,
    PipelineReasonCode.SOURCE_SELECTOR_FAILED,
    PipelineReasonCode.SOURCE_PARSE_FAILED,
    PipelineReasonCode.SOURCE_ZERO_YIELD,
    PipelineReasonCode.DATA_STALE,
}

_EVENT_FIELDS = (
    "event_key",
    "run_id",
    "release_id",
    "event_type",
    "commit_sha",
    "occurred_at",
    "stage",
    "source",
    "input_count",
    "output_count",
    "error_count",
    "attempt_count",
    "inserted_count",
    "selector_failure_count",
    "parsing_failure_count",
    "latest_successful_article_at",
    "extractor_versions",
    "lease_expires_at",
    "reason_code",
    "data_sequence",
    "promotion_sequence",
    "manifest_sha256",
    "rollback_of_promotion_sequence",
)


def _required_text(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise EventValidationError(f"{field} must contain at most {maximum} characters")
    return value


def _optional_text(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field=field, maximum=maximum)


def _utc_datetime(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventValidationError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _database_utc(value: datetime | None) -> datetime | None:
    """Normalize timestamps read from SQLite, which drops timezone metadata."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _nonnegative(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EventValidationError(f"{field} must be a non-negative integer")
    return value


def _positive(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EventValidationError(f"{field} must be a positive integer")
    return value


def _sha256(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise EventValidationError(f"{field} must be a lower-case SHA-256 hex digest")
    return value


def _extractor_versions(value: dict[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise EventValidationError("extractor_versions must be a string-to-string mapping")

    normalized: dict[str, str] = {}
    for name, version in value.items():
        if not isinstance(name, str) or not name.strip():
            raise EventValidationError("extractor version names must be non-empty strings")
        if not isinstance(version, str) or not version.strip():
            raise EventValidationError("extractor version values must be non-empty strings")
        normalized[name] = version
    return dict(sorted(normalized.items()))


def _validate_scope(
    event_type: PipelineEventType,
    *,
    stage: str | None,
    source: str | None,
    release_id: str | None,
) -> None:
    if event_type in _RUN_EVENTS:
        if stage is not None or source is not None:
            raise EventValidationError("run events cannot name a stage or source")
        return

    if event_type in _STAGE_EVENTS:
        if stage is None:
            raise EventValidationError("stage events require stage")
        if source is not None:
            raise EventValidationError("stage events cannot name a source")
        return

    if event_type in _SOURCE_EVENTS:
        if stage is None or source is None:
            raise EventValidationError("source events require both stage and source")
        return

    if event_type in _RELEASE_EVENTS:
        if release_id is None:
            raise EventValidationError("release events require release_id")
        if stage is not None or source is not None:
            raise EventValidationError("release events cannot name a stage or source")


def _validate_reason(
    event_type: PipelineEventType,
    reason_code: PipelineReasonCode | None,
) -> None:
    if event_type in _FAILURE_EVENTS and reason_code is None:
        raise EventValidationError(f"{event_type.value} requires a safe reason_code")
    if event_type == PipelineEventType.RUN_ABANDONED:
        if reason_code != PipelineReasonCode.LEASE_EXPIRED:
            raise EventValidationError("run_abandoned requires reason_code='lease_expired'")
        return

    if event_type == PipelineEventType.SOURCE_SUCCEEDED:
        if reason_code is not None and reason_code not in _SOURCE_WARNING_REASONS:
            raise EventValidationError(
                "source_succeeded accepts only source warning reason codes"
            )
        return

    if event_type not in _FAILURE_EVENTS and reason_code is not None:
        raise EventValidationError(f"{event_type.value} cannot carry a failure reason")


def _validate_source_summary(
    event_type: PipelineEventType,
    *,
    output_count: int | None,
    attempt_count: int | None,
    inserted_count: int | None,
    selector_failure_count: int | None,
    parsing_failure_count: int | None,
    latest_successful_article_at: datetime | None,
    reason_code: PipelineReasonCode | None,
) -> None:
    source_values = (
        attempt_count,
        inserted_count,
        selector_failure_count,
        parsing_failure_count,
        latest_successful_article_at,
    )
    if event_type not in _SOURCE_EVENTS:
        if any(value is not None for value in source_values):
            raise EventValidationError("source-only measurements require a source event")
        return

    if event_type == PipelineEventType.SOURCE_STARTED:
        if any(value is not None for value in source_values):
            raise EventValidationError("source_started cannot claim terminal measurements")
        return

    required = {
        "attempt_count": attempt_count,
        "output_count": output_count,
        "inserted_count": inserted_count,
        "selector_failure_count": selector_failure_count,
        "parsing_failure_count": parsing_failure_count,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        joined = ", ".join(missing)
        raise EventValidationError(f"terminal source events require {joined}")

    if event_type == PipelineEventType.SOURCE_SUCCEEDED:
        assert output_count is not None
        if output_count == 0 and reason_code not in _SOURCE_WARNING_REASONS:
            raise EventValidationError(
                "a zero-yield source must record a closed source warning reason"
            )
        if output_count > 0 and reason_code == PipelineReasonCode.SOURCE_ZERO_YIELD:
            raise EventValidationError("source_zero_yield contradicts a positive output_count")
        # Some publishers omit a usable publication timestamp.  Successful
        # yield is still measurable; keeping this NULL is more honest than
        # substituting collection time and making the source look fresher.


def _validate_release_summary(
    event_type: PipelineEventType,
    *,
    data_sequence: int | None,
    promotion_sequence: int | None,
    manifest_sha256: str | None,
    rollback_of_promotion_sequence: int | None,
) -> None:
    release_values = (
        data_sequence,
        promotion_sequence,
        manifest_sha256,
        rollback_of_promotion_sequence,
    )
    if event_type not in _RELEASE_EVENTS:
        if any(value is not None for value in release_values):
            raise EventValidationError("release-only fields require a release event")
        return

    if event_type == PipelineEventType.RELEASE_RESERVED:
        if any(value is not None for value in release_values):
            raise EventValidationError(
                "release_reserved uses its event id as the data sequence"
            )
        return

    required = {
        "data_sequence": data_sequence,
        "manifest_sha256": manifest_sha256,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise EventValidationError(
            f"{event_type.value} requires {', '.join(missing)}"
        )

    events_with_completed_promotion = {
        PipelineEventType.RELEASE_PUBLISHED,
        PipelineEventType.RELEASE_FAILED,
        PipelineEventType.RELEASE_SUPERSEDED,
    }
    if event_type in events_with_completed_promotion and promotion_sequence is None:
        raise EventValidationError(
            f"{event_type.value} requires promotion_sequence"
        )
    if event_type in {
        PipelineEventType.RELEASE_CANDIDATE_CREATED,
        PipelineEventType.PROMOTION_STARTED,
    } and promotion_sequence is not None:
        raise EventValidationError(
            f"{event_type.value} cannot carry promotion_sequence; its event id "
            "is the reserved sequence"
        )

    rollback_events = {
        PipelineEventType.PROMOTION_STARTED,
        PipelineEventType.RELEASE_PUBLISHED,
        PipelineEventType.RELEASE_FAILED,
    }
    if rollback_of_promotion_sequence is not None and event_type not in rollback_events:
        raise EventValidationError(
            "rollback_of_promotion_sequence requires a promotion event"
        )


def _validate_lease(
    event_type: PipelineEventType,
    *,
    occurred_at: datetime,
    lease_expires_at: datetime | None,
) -> None:
    if event_type in {PipelineEventType.RUN_STARTED, PipelineEventType.RUN_HEARTBEAT}:
        if lease_expires_at is None:
            raise EventValidationError(f"{event_type.value} requires lease_expires_at")
        if lease_expires_at <= occurred_at:
            raise EventValidationError("lease_expires_at must be later than occurred_at")
        return
    if lease_expires_at is not None:
        raise EventValidationError("only run_started and run_heartbeat may carry a lease")


def _event_values_match(existing: PipelineEventORM, values: dict[str, Any]) -> bool:
    for field in _EVENT_FIELDS:
        existing_value = getattr(existing, field)
        candidate_value = values[field]
        if field in {"occurred_at", "latest_successful_article_at", "lease_expires_at"}:
            existing_value = _database_utc(existing_value)
            candidate_value = _database_utc(candidate_value)
        if existing_value != candidate_value:
            return False
    return True


def _run_events(session: Session, run_id: str) -> list[PipelineEventORM]:
    return list(
        session.scalars(
            select(PipelineEventORM)
            .where(PipelineEventORM.run_id == run_id)
            .order_by(PipelineEventORM.occurred_at.asc(), PipelineEventORM.id.asc())
        )
    )


def _release_events(session: Session, release_id: str) -> list[PipelineEventORM]:
    return list(
        session.scalars(
            select(PipelineEventORM)
            .where(PipelineEventORM.release_id == release_id)
            .order_by(PipelineEventORM.occurred_at.asc(), PipelineEventORM.id.asc())
        )
    )


def _validate_transition(
    session: Session,
    *,
    run_id: str,
    event_type: PipelineEventType,
    stage: str | None,
    source: str | None,
    release_id: str | None,
    data_sequence: int | None,
    promotion_sequence: int | None,
    manifest_sha256: str | None,
    rollback_of_promotion_sequence: int | None,
) -> None:
    history = _run_events(session, run_id)
    run_started = any(row.event_type == PipelineEventType.RUN_STARTED.value for row in history)

    if event_type == PipelineEventType.RUN_STARTED:
        if history:
            raise InvalidEventTransition(f"run {run_id!r} already has event history")
        return

    if not run_started:
        raise InvalidEventTransition(f"run {run_id!r} has no run_started event")

    terminal = next(
        (row for row in history if row.event_type in {item.value for item in _RUN_TERMINAL_EVENTS}),
        None,
    )
    if terminal is not None and event_type not in _RELEASE_EVENTS:
        raise InvalidEventTransition(
            f"run {run_id!r} is already terminal ({terminal.event_type})"
        )

    if event_type == PipelineEventType.STAGE_STARTED:
        already_started = any(
            row.event_type == PipelineEventType.STAGE_STARTED.value and row.stage == stage
            for row in history
        )
        if already_started:
            raise InvalidEventTransition(f"stage {stage!r} already has a start event")

    if event_type in _SOURCE_EVENTS:
        stage_started = any(
            row.event_type == PipelineEventType.STAGE_STARTED.value and row.stage == stage
            for row in history
        )
        if not stage_started:
            raise InvalidEventTransition(
                f"source {source!r} belongs to stage {stage!r}, which has not started"
            )
        stage_finished = any(
            row.event_type in {item.value for item in _STAGE_TERMINAL_EVENTS}
            and row.stage == stage
            for row in history
        )
        if stage_finished:
            raise InvalidEventTransition(
                f"source {source!r} cannot change after stage {stage!r} finished"
            )

    if event_type == PipelineEventType.SOURCE_STARTED:
        already_started = any(
            row.event_type == PipelineEventType.SOURCE_STARTED.value
            and row.stage == stage
            and row.source == source
            for row in history
        )
        if already_started:
            raise InvalidEventTransition(f"source {source!r} already has a start event")

    if event_type in _RUN_TERMINAL_EVENTS:
        prior_terminal = any(
            row.event_type in {item.value for item in _RUN_TERMINAL_EVENTS}
            for row in history
        )
        if prior_terminal:
            raise InvalidEventTransition(f"run {run_id!r} already has a terminal event")

    if event_type in _STAGE_TERMINAL_EVENTS:
        started = any(
            row.event_type == PipelineEventType.STAGE_STARTED.value and row.stage == stage
            for row in history
        )
        if not started:
            raise InvalidEventTransition(f"stage {stage!r} has no stage_started event")
        finished = any(
            row.event_type in {item.value for item in _STAGE_TERMINAL_EVENTS}
            and row.stage == stage
            for row in history
        )
        if finished:
            raise InvalidEventTransition(f"stage {stage!r} already has a terminal event")

    if event_type in _SOURCE_TERMINAL_EVENTS:
        started = any(
            row.event_type == PipelineEventType.SOURCE_STARTED.value
            and row.stage == stage
            and row.source == source
            for row in history
        )
        if not started:
            raise InvalidEventTransition(f"source {source!r} has no source_started event")
        finished = any(
            row.event_type in {item.value for item in _SOURCE_TERMINAL_EVENTS}
            and row.stage == stage
            and row.source == source
            for row in history
        )
        if finished:
            raise InvalidEventTransition(f"source {source!r} already has a terminal event")

    if event_type not in _RELEASE_EVENTS:
        return

    assert release_id is not None
    release_history = _release_events(session, release_id)
    if event_type == PipelineEventType.RELEASE_RESERVED:
        if release_history:
            raise InvalidEventTransition(
                f"release {release_id!r} already has event history"
            )
        return

    reservation = next(
        (
            row
            for row in release_history
            if row.event_type == PipelineEventType.RELEASE_RESERVED.value
        ),
        None,
    )
    if reservation is None:
        raise InvalidEventTransition(f"release {release_id!r} has no reservation")

    candidate = next(
        (
            row
            for row in release_history
            if row.event_type
            == PipelineEventType.RELEASE_CANDIDATE_CREATED.value
        ),
        None,
    )
    if event_type == PipelineEventType.RELEASE_CANDIDATE_CREATED:
        if candidate is not None:
            raise InvalidEventTransition(
                f"release {release_id!r} already has a candidate event"
            )
        if data_sequence != reservation.id:
            raise InvalidEventTransition(
                "candidate data_sequence must equal its reservation event id"
            )
        return

    if candidate is None:
        raise InvalidEventTransition(f"release {release_id!r} has no candidate event")
    if (
        candidate.data_sequence != data_sequence
        or candidate.manifest_sha256 != manifest_sha256
    ):
        raise InvalidEventTransition(
            f"release {release_id!r} metadata differs from its candidate event"
        )

    if event_type in {
        PipelineEventType.PROMOTION_STARTED,
        PipelineEventType.RELEASE_SUPERSEDED,
    }:
        return

    assert promotion_sequence is not None
    promotion = next(
        (
            row
            for row in release_history
            if row.id == promotion_sequence
            and row.event_type == PipelineEventType.PROMOTION_STARTED.value
        ),
        None,
    )
    if promotion is None:
        raise InvalidEventTransition(
            f"promotion sequence {promotion_sequence} has no promotion_started event"
        )
    if promotion.rollback_of_promotion_sequence != rollback_of_promotion_sequence:
        raise InvalidEventTransition(
            "promotion terminal event changes rollback metadata"
        )
    prior_terminal = any(
        row.promotion_sequence == promotion_sequence
        and row.event_type
        in {
            PipelineEventType.RELEASE_PUBLISHED.value,
            PipelineEventType.RELEASE_FAILED.value,
        }
        for row in release_history
    )
    if prior_terminal:
        raise InvalidEventTransition(
            f"promotion sequence {promotion_sequence} is already terminal"
        )


def append_pipeline_event(
    session: Session,
    *,
    event_key: str,
    run_id: str,
    event_type: PipelineEventType | str,
    commit_sha: str,
    occurred_at: datetime | None = None,
    release_id: str | None = None,
    stage: str | None = None,
    source: str | None = None,
    input_count: int | None = None,
    output_count: int | None = None,
    error_count: int | None = None,
    attempt_count: int | None = None,
    inserted_count: int | None = None,
    selector_failure_count: int | None = None,
    parsing_failure_count: int | None = None,
    latest_successful_article_at: datetime | None = None,
    extractor_versions: dict[str, str] | None = None,
    lease_expires_at: datetime | None = None,
    reason_code: PipelineReasonCode | str | None = None,
    data_sequence: int | None = None,
    promotion_sequence: int | None = None,
    manifest_sha256: str | None = None,
    rollback_of_promotion_sequence: int | None = None,
) -> PipelineEventORM:
    """Validate and append one event, returning an identical retry unchanged.

    ``event_key`` is the caller's stable idempotency key.  Reusing it with the
    exact same payload is safe; reusing it with different content is rejected
    because silently changing history would defeat the ledger.
    """
    try:
        normalized_event_type = PipelineEventType(event_type)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"unknown event_type: {event_type!r}") from exc
    try:
        normalized_reason = (
            PipelineReasonCode(reason_code) if reason_code is not None else None
        )
    except (TypeError, ValueError) as exc:
        raise EventValidationError("reason_code must be a known safe code") from exc

    event_key = _required_text(event_key, field="event_key", maximum=180)
    run_id = _required_text(run_id, field="run_id", maximum=100)
    commit_sha = _required_text(commit_sha, field="commit_sha", maximum=64)
    release_id = _optional_text(release_id, field="release_id", maximum=100)
    stage = _optional_text(stage, field="stage", maximum=64)
    source = _optional_text(source, field="source", maximum=100)
    data_sequence = _positive(data_sequence, field="data_sequence")
    promotion_sequence = _positive(promotion_sequence, field="promotion_sequence")
    manifest_sha256 = _sha256(manifest_sha256, field="manifest_sha256")
    rollback_of_promotion_sequence = _positive(
        rollback_of_promotion_sequence,
        field="rollback_of_promotion_sequence",
    )

    existing = session.scalar(
        select(PipelineEventORM).where(PipelineEventORM.event_key == event_key)
    )
    if occurred_at is None and existing is not None:
        normalized_occurred_at = _database_utc(existing.occurred_at)
        assert normalized_occurred_at is not None
    else:
        normalized_occurred_at = _utc_datetime(
            occurred_at or datetime.now(timezone.utc), field="occurred_at"
        )

    normalized_latest = (
        _utc_datetime(latest_successful_article_at, field="latest_successful_article_at")
        if latest_successful_article_at is not None
        else None
    )
    normalized_lease = (
        _utc_datetime(lease_expires_at, field="lease_expires_at")
        if lease_expires_at is not None
        else None
    )
    counts = {
        "input_count": _nonnegative(input_count, field="input_count"),
        "output_count": _nonnegative(output_count, field="output_count"),
        "error_count": _nonnegative(error_count, field="error_count"),
        "attempt_count": _nonnegative(attempt_count, field="attempt_count"),
        "inserted_count": _nonnegative(inserted_count, field="inserted_count"),
        "selector_failure_count": _nonnegative(
            selector_failure_count, field="selector_failure_count"
        ),
        "parsing_failure_count": _nonnegative(
            parsing_failure_count, field="parsing_failure_count"
        ),
    }

    _validate_scope(
        normalized_event_type,
        stage=stage,
        source=source,
        release_id=release_id,
    )
    _validate_reason(normalized_event_type, normalized_reason)
    _validate_source_summary(
        normalized_event_type,
        output_count=counts["output_count"],
        attempt_count=counts["attempt_count"],
        inserted_count=counts["inserted_count"],
        selector_failure_count=counts["selector_failure_count"],
        parsing_failure_count=counts["parsing_failure_count"],
        latest_successful_article_at=normalized_latest,
        reason_code=normalized_reason,
    )
    _validate_lease(
        normalized_event_type,
        occurred_at=normalized_occurred_at,
        lease_expires_at=normalized_lease,
    )
    _validate_release_summary(
        normalized_event_type,
        data_sequence=data_sequence,
        promotion_sequence=promotion_sequence,
        manifest_sha256=manifest_sha256,
        rollback_of_promotion_sequence=rollback_of_promotion_sequence,
    )

    values: dict[str, Any] = {
        "event_key": event_key,
        "run_id": run_id,
        "release_id": release_id,
        "event_type": normalized_event_type.value,
        "commit_sha": commit_sha,
        "occurred_at": normalized_occurred_at,
        "stage": stage,
        "source": source,
        **counts,
        "latest_successful_article_at": normalized_latest,
        "extractor_versions": _extractor_versions(extractor_versions),
        "lease_expires_at": normalized_lease,
        "reason_code": normalized_reason.value if normalized_reason is not None else None,
        "data_sequence": data_sequence,
        "promotion_sequence": promotion_sequence,
        "manifest_sha256": manifest_sha256,
        "rollback_of_promotion_sequence": rollback_of_promotion_sequence,
    }

    if existing is not None:
        if _event_values_match(existing, values):
            return existing
        raise EventKeyConflict(f"event_key {event_key!r} already has different content")

    _validate_transition(
        session,
        run_id=run_id,
        event_type=normalized_event_type,
        stage=stage,
        source=source,
        release_id=release_id,
        data_sequence=data_sequence,
        promotion_sequence=promotion_sequence,
        manifest_sha256=manifest_sha256,
        rollback_of_promotion_sequence=rollback_of_promotion_sequence,
    )

    row = PipelineEventORM(**values)
    session.add(row)
    session.flush()
    return row


def load_run_events(session: Session, run_id: str) -> list[PipelineEventORM]:
    """Return one run's immutable history in deterministic order."""
    run_id = _required_text(run_id, field="run_id", maximum=100)
    return _run_events(session, run_id)


def abandon_expired_runs(
    session: Session,
    *,
    now: datetime,
    monitor_commit_sha: str,
) -> list[PipelineEventORM]:
    """Append ``run_abandoned`` for each unterminated, expired lease.

    This is intentionally a monitor action instead of a projection shortcut.
    Once emitted, abandonment is an auditable fact even if the original
    runner disappeared before it could record a final status.
    """
    now = _utc_datetime(now, field="now")
    monitor_commit_sha = _required_text(
        monitor_commit_sha, field="monitor_commit_sha", maximum=64
    )
    rows = list(
        session.scalars(
            select(PipelineEventORM)
            .where(PipelineEventORM.event_type.in_([item.value for item in _RUN_EVENTS]))
            .order_by(
                PipelineEventORM.run_id.asc(),
                PipelineEventORM.occurred_at.asc(),
                PipelineEventORM.id.asc(),
            )
        )
    )

    by_run: dict[str, list[PipelineEventORM]] = {}
    for row in rows:
        by_run.setdefault(row.run_id, []).append(row)

    abandoned: list[PipelineEventORM] = []
    terminal_values = {item.value for item in _RUN_TERMINAL_EVENTS}
    lease_values = {
        PipelineEventType.RUN_STARTED.value,
        PipelineEventType.RUN_HEARTBEAT.value,
    }
    for run_id, history in by_run.items():
        if any(row.event_type in terminal_values for row in history):
            continue

        lease_rows = [row for row in history if row.event_type in lease_values]
        if not lease_rows:
            continue
        latest_lease_row = lease_rows[-1]
        lease_expires_at = _database_utc(latest_lease_row.lease_expires_at)
        if lease_expires_at is None or lease_expires_at > now:
            continue

        event_key = (
            f"{run_id}:run_abandoned:{lease_expires_at.isoformat(timespec='microseconds')}"
        )
        abandoned.append(
            append_pipeline_event(
                session,
                event_key=event_key,
                run_id=run_id,
                release_id=latest_lease_row.release_id,
                event_type=PipelineEventType.RUN_ABANDONED,
                commit_sha=monitor_commit_sha,
                occurred_at=now,
                reason_code=PipelineReasonCode.LEASE_EXPIRED,
            )
        )

    return abandoned
