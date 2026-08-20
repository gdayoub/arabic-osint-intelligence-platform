"""Pure projections from immutable pipeline events to current health."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from src.ops.events import PipelineEventType, PipelineReasonCode, safe_reason_message
from src.ops.ledger import load_run_events
from src.store.orm import PipelineEventORM


@dataclass(frozen=True, slots=True)
class HealthReason:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class StageHealth:
    name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    input_count: int | None
    output_count: int | None
    error_count: int | None
    reason: HealthReason | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": _isoformat(self.started_at),
            "finished_at": _isoformat(self.finished_at),
            "duration_seconds": self.duration_seconds,
            "input_count": self.input_count,
            "output_count": self.output_count,
            "error_count": self.error_count,
            "reason": self.reason.to_dict() if self.reason is not None else None,
        }


@dataclass(frozen=True, slots=True)
class SourceHealth:
    name: str
    stage: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    attempt_count: int | None
    yielded_count: int | None
    inserted_count: int | None
    error_count: int | None
    selector_failure_count: int | None
    parsing_failure_count: int | None
    latest_successful_article_at: datetime | None
    reason: HealthReason | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "status": self.status,
            "started_at": _isoformat(self.started_at),
            "finished_at": _isoformat(self.finished_at),
            "duration_seconds": self.duration_seconds,
            "attempt_count": self.attempt_count,
            "yielded_count": self.yielded_count,
            "inserted_count": self.inserted_count,
            "error_count": self.error_count,
            "selector_failure_count": self.selector_failure_count,
            "parsing_failure_count": self.parsing_failure_count,
            "latest_successful_article_at": _isoformat(
                self.latest_successful_article_at
            ),
            "reason": self.reason.to_dict() if self.reason is not None else None,
        }


@dataclass(frozen=True, slots=True)
class RunHealth:
    run_id: str
    release_id: str | None
    commit_sha: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    lease_expires_at: datetime | None
    extractor_versions: dict[str, str]
    reason: HealthReason | None
    stages: tuple[StageHealth, ...]
    sources: tuple[SourceHealth, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready projection containing no private error text."""
        return {
            "run_id": self.run_id,
            "release_id": self.release_id,
            "commit_sha": self.commit_sha,
            "status": self.status,
            "started_at": _isoformat(self.started_at),
            "finished_at": _isoformat(self.finished_at),
            "duration_seconds": self.duration_seconds,
            "lease_expires_at": _isoformat(self.lease_expires_at),
            "extractor_versions": dict(self.extractor_versions),
            "reason": self.reason.to_dict() if self.reason is not None else None,
            "stages": [stage.to_dict() for stage in self.stages],
            "sources": [source.to_dict() for source in self.sources],
        }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite removes timezone metadata from DateTime values.  The write
        # path accepted only aware UTC inputs, so a naive value read back from
        # SQLite still has an unambiguous meaning in local tests.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def _duration(started_at: datetime, finished_at: datetime | None) -> float | None:
    start = _as_utc(started_at)
    finish = _as_utc(finished_at)
    assert start is not None
    if finish is None:
        return None
    return max(0.0, (finish - start).total_seconds())


def _reason(code: PipelineReasonCode | str | None) -> HealthReason | None:
    if code is None:
        return None
    normalized = PipelineReasonCode(code)
    message = safe_reason_message(normalized)
    assert message is not None
    return HealthReason(code=normalized.value, message=message)


def _first_event(
    events: list[PipelineEventORM], event_type: PipelineEventType
) -> PipelineEventORM | None:
    return next((row for row in events if row.event_type == event_type.value), None)


def _terminal_event(
    events: list[PipelineEventORM], event_types: set[PipelineEventType]
) -> PipelineEventORM | None:
    terminal_values = {item.value for item in event_types}
    return next((row for row in reversed(events) if row.event_type in terminal_values), None)


def _incomplete_status(
    run_terminal: PipelineEventORM | None,
) -> tuple[str, HealthReason | None]:
    if run_terminal is None:
        return "running", None
    if run_terminal.event_type == PipelineEventType.RUN_ABANDONED.value:
        return "abandoned", _reason(PipelineReasonCode.LEASE_EXPIRED)
    if run_terminal.event_type == PipelineEventType.RUN_SUCCEEDED.value:
        return "partial", _reason(PipelineReasonCode.UPSTREAM_STAGE_FAILED)
    return "failed", _reason(PipelineReasonCode.UPSTREAM_STAGE_FAILED)


def _project_stage(
    name: str,
    events: list[PipelineEventORM],
    run_terminal: PipelineEventORM | None,
) -> StageHealth:
    started = _first_event(events, PipelineEventType.STAGE_STARTED)
    if started is None:
        raise ValueError(f"stage {name!r} has no stage_started event")
    terminal = _terminal_event(
        events,
        {PipelineEventType.STAGE_SUCCEEDED, PipelineEventType.STAGE_FAILED},
    )

    if terminal is None:
        status, reason = _incomplete_status(run_terminal)
        counts_from = started
    elif terminal.event_type == PipelineEventType.STAGE_FAILED.value:
        status = "failed"
        reason = _reason(terminal.reason_code)
        counts_from = terminal
    elif terminal.error_count is not None and terminal.error_count > 0:
        status = "degraded"
        reason = _reason(PipelineReasonCode.UNEXPECTED_ERROR)
        counts_from = terminal
    else:
        status = "succeeded"
        reason = None
        counts_from = terminal

    started_at = _as_utc(started.occurred_at)
    assert started_at is not None
    finished_at = _as_utc(terminal.occurred_at) if terminal is not None else None
    return StageHealth(
        name=name,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=_duration(started_at, finished_at),
        input_count=counts_from.input_count,
        output_count=counts_from.output_count,
        error_count=counts_from.error_count,
        reason=reason,
    )


def _source_warning_reason(
    terminal: PipelineEventORM,
    *,
    now: datetime,
    stale_after: timedelta | None,
) -> HealthReason | None:
    if terminal.reason_code is not None:
        return _reason(terminal.reason_code)
    if terminal.output_count == 0:
        return _reason(PipelineReasonCode.SOURCE_ZERO_YIELD)
    if terminal.selector_failure_count is not None and terminal.selector_failure_count > 0:
        return _reason(PipelineReasonCode.SOURCE_SELECTOR_FAILED)
    if terminal.parsing_failure_count is not None and terminal.parsing_failure_count > 0:
        return _reason(PipelineReasonCode.SOURCE_PARSE_FAILED)
    if terminal.error_count is not None and terminal.error_count > 0:
        return _reason(PipelineReasonCode.UNEXPECTED_ERROR)

    latest_article = _as_utc(terminal.latest_successful_article_at)
    if stale_after is not None and latest_article is not None:
        if now - latest_article > stale_after:
            return _reason(PipelineReasonCode.DATA_STALE)
    return None


def _project_source(
    stage: str,
    name: str,
    events: list[PipelineEventORM],
    run_terminal: PipelineEventORM | None,
    *,
    now: datetime,
    stale_after: timedelta | None,
) -> SourceHealth:
    started = _first_event(events, PipelineEventType.SOURCE_STARTED)
    if started is None:
        raise ValueError(f"source {name!r} has no source_started event")
    terminal = _terminal_event(
        events,
        {PipelineEventType.SOURCE_SUCCEEDED, PipelineEventType.SOURCE_FAILED},
    )

    if terminal is None:
        status, reason = _incomplete_status(run_terminal)
        counts_from = started
    elif terminal.event_type == PipelineEventType.SOURCE_FAILED.value:
        status = "failed"
        reason = _reason(terminal.reason_code)
        counts_from = terminal
    else:
        reason = _source_warning_reason(
            terminal,
            now=now,
            stale_after=stale_after,
        )
        status = "degraded" if reason is not None else "healthy"
        counts_from = terminal

    started_at = _as_utc(started.occurred_at)
    assert started_at is not None
    finished_at = _as_utc(terminal.occurred_at) if terminal is not None else None
    return SourceHealth(
        name=name,
        stage=stage,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=_duration(started_at, finished_at),
        attempt_count=counts_from.attempt_count,
        yielded_count=counts_from.output_count,
        inserted_count=counts_from.inserted_count,
        error_count=counts_from.error_count,
        selector_failure_count=counts_from.selector_failure_count,
        parsing_failure_count=counts_from.parsing_failure_count,
        latest_successful_article_at=_as_utc(
            counts_from.latest_successful_article_at
        ),
        reason=reason,
    )


def _overall_status(
    run_terminal: PipelineEventORM | None,
    lease_expires_at: datetime | None,
    stages: tuple[StageHealth, ...],
    sources: tuple[SourceHealth, ...],
    *,
    now: datetime,
) -> tuple[str, HealthReason | None]:
    if run_terminal is None:
        if lease_expires_at is not None and lease_expires_at <= now:
            return "overdue", _reason(PipelineReasonCode.LEASE_EXPIRED)
        return "running", None

    if run_terminal.event_type == PipelineEventType.RUN_FAILED.value:
        return "failed", _reason(run_terminal.reason_code)
    if run_terminal.event_type == PipelineEventType.RUN_ABANDONED.value:
        return "abandoned", _reason(run_terminal.reason_code)

    child_states = [stage.status for stage in stages] + [source.status for source in sources]
    child_reasons = [stage.reason for stage in stages] + [source.reason for source in sources]
    if any(status in {"failed", "abandoned"} for status in child_states):
        reason = next((item for item in child_reasons if item is not None), None)
        return "failed", reason or _reason(PipelineReasonCode.UPSTREAM_STAGE_FAILED)
    if any(status in {"running", "partial"} for status in child_states):
        return "partial", _reason(PipelineReasonCode.UPSTREAM_STAGE_FAILED)
    if any(status == "degraded" for status in child_states):
        reason = next((item for item in child_reasons if item is not None), None)
        return "degraded", reason
    return "healthy", None


def project_run_health(
    event_rows: Iterable[PipelineEventORM],
    *,
    now: datetime | None = None,
    stale_after: timedelta | None = None,
) -> RunHealth:
    """Rebuild run, stage, and source health without mutating event rows."""
    now = _as_utc(now or datetime.now(timezone.utc))
    assert now is not None
    if stale_after is not None and stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")

    events = sorted(
        event_rows,
        key=lambda row: (_as_utc(row.occurred_at), row.id or 0),
    )
    if not events:
        raise ValueError("cannot project health from an empty event stream")
    run_ids = {row.run_id for row in events}
    if len(run_ids) != 1:
        raise ValueError("health projection requires events from exactly one run")

    started = _first_event(events, PipelineEventType.RUN_STARTED)
    if started is None:
        raise ValueError("run history has no run_started event")
    run_terminal = _terminal_event(
        events,
        {
            PipelineEventType.RUN_SUCCEEDED,
            PipelineEventType.RUN_FAILED,
            PipelineEventType.RUN_ABANDONED,
        },
    )

    latest_lease_event = next(
        (
            row
            for row in reversed(events)
            if row.event_type
            in {
                PipelineEventType.RUN_STARTED.value,
                PipelineEventType.RUN_HEARTBEAT.value,
            }
        ),
        started,
    )
    lease_expires_at = _as_utc(latest_lease_event.lease_expires_at)

    stage_events: dict[str, list[PipelineEventORM]] = {}
    source_events: dict[tuple[str, str], list[PipelineEventORM]] = {}
    for row in events:
        if row.event_type in {
            PipelineEventType.STAGE_STARTED.value,
            PipelineEventType.STAGE_SUCCEEDED.value,
            PipelineEventType.STAGE_FAILED.value,
        }:
            assert row.stage is not None
            stage_events.setdefault(row.stage, []).append(row)
        if row.event_type in {
            PipelineEventType.SOURCE_STARTED.value,
            PipelineEventType.SOURCE_SUCCEEDED.value,
            PipelineEventType.SOURCE_FAILED.value,
        }:
            assert row.stage is not None and row.source is not None
            source_events.setdefault((row.stage, row.source), []).append(row)

    stages = tuple(
        _project_stage(name, rows, run_terminal)
        for name, rows in sorted(stage_events.items())
    )
    sources = tuple(
        _project_source(
            stage,
            name,
            rows,
            run_terminal,
            now=now,
            stale_after=stale_after,
        )
        for (stage, name), rows in sorted(source_events.items())
    )
    status, reason = _overall_status(
        run_terminal,
        lease_expires_at,
        stages,
        sources,
        now=now,
    )

    extractor_versions: dict[str, str] = {}
    for row in events:
        extractor_versions.update(row.extractor_versions or {})

    release_id = next(
        (row.release_id for row in reversed(events) if row.release_id is not None),
        None,
    )
    started_at = _as_utc(started.occurred_at)
    assert started_at is not None
    finished_at = _as_utc(run_terminal.occurred_at) if run_terminal is not None else None
    return RunHealth(
        run_id=started.run_id,
        release_id=release_id,
        commit_sha=started.commit_sha,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=_duration(started_at, finished_at),
        lease_expires_at=lease_expires_at,
        extractor_versions=dict(sorted(extractor_versions.items())),
        reason=reason,
        stages=stages,
        sources=sources,
    )


def load_run_health(
    session: Session,
    run_id: str,
    *,
    now: datetime | None = None,
    stale_after: timedelta | None = None,
) -> RunHealth:
    """Load and project one run in one call for CLI/API consumers."""
    return project_run_health(
        load_run_events(session, run_id),
        now=now,
        stale_after=stale_after,
    )
