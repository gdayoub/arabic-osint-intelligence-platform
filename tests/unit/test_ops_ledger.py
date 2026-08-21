"""Regression tests for append-only operational event recording."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.ops.events import PipelineEventType, PipelineReasonCode
from src.ops.ledger import (
    EventKeyConflict,
    EventValidationError,
    InvalidEventTransition,
    abandon_expired_runs,
    append_pipeline_event,
    load_run_events,
)
from src.store.orm import AppendOnlyEventError, PipelineEventORM


BASE_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _start_run(
    session,  # noqa: ANN001
    run_id: str,
    *,
    started_at: datetime = BASE_TIME,
    lease_seconds: int = 60,
) -> PipelineEventORM:
    return append_pipeline_event(
        session,
        event_key=f"{run_id}:run-started",
        run_id=run_id,
        event_type=PipelineEventType.RUN_STARTED,
        commit_sha="0123456789abcdef",
        occurred_at=started_at,
        lease_expires_at=started_at + timedelta(seconds=lease_seconds),
        extractor_versions={"classifier": "2.0.1", "gazetteer": "1.0.0"},
    )


def test_append_is_idempotent_but_event_key_cannot_change_history(session):  # noqa: ANN001
    first = _start_run(session, "run-idempotent")
    retry = append_pipeline_event(
        session,
        event_key="run-idempotent:run-started",
        run_id="run-idempotent",
        event_type="run_started",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME,
        lease_expires_at=BASE_TIME + timedelta(seconds=60),
        extractor_versions={"gazetteer": "1.0.0", "classifier": "2.0.1"},
    )

    assert retry.id == first.id
    assert len(load_run_events(session, "run-idempotent")) == 1

    with pytest.raises(EventKeyConflict, match="different content"):
        append_pipeline_event(
            session,
            event_key="run-idempotent:run-started",
            run_id="run-idempotent",
            event_type="run_started",
            commit_sha="different-commit",
            occurred_at=BASE_TIME,
            lease_expires_at=BASE_TIME + timedelta(seconds=60),
            extractor_versions={"gazetteer": "1.0.0", "classifier": "2.0.1"},
        )


def test_validation_rejects_unsafe_reasons_bad_counts_and_missing_scope(session):  # noqa: ANN001
    with pytest.raises(EventValidationError, match="known safe code"):
        append_pipeline_event(
            session,
            event_key="unsafe-reason",
            run_id="run-unsafe",
            event_type="run_failed",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME,
            reason_code="Traceback: postgresql://user:password@example.test/db",
        )

    with pytest.raises(EventValidationError, match="non-negative"):
        append_pipeline_event(
            session,
            event_key="negative-count",
            run_id="run-negative",
            event_type="run_started",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME,
            lease_expires_at=BASE_TIME + timedelta(seconds=60),
            error_count=-1,
        )

    _start_run(session, "run-scope")
    with pytest.raises(EventValidationError, match="require stage"):
        append_pipeline_event(
            session,
            event_key="missing-stage",
            run_id="run-scope",
            event_type="stage_started",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME + timedelta(seconds=1),
        )


def test_terminal_events_require_their_start_event(session):  # noqa: ANN001
    _start_run(session, "run-transition")

    with pytest.raises(InvalidEventTransition, match="no stage_started"):
        append_pipeline_event(
            session,
            event_key="run-transition:process:succeeded",
            run_id="run-transition",
            event_type="stage_succeeded",
            stage="process",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME + timedelta(seconds=5),
        )


def test_duplicate_starts_and_source_without_started_stage_are_rejected(session):  # noqa: ANN001
    _start_run(session, "run-duplicate-start")
    append_pipeline_event(
        session,
        event_key="run-duplicate-start:ingest:started:1",
        run_id="run-duplicate-start",
        event_type="stage_started",
        stage="ingest",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=1),
    )

    with pytest.raises(InvalidEventTransition, match="already has a start event"):
        append_pipeline_event(
            session,
            event_key="run-duplicate-start:ingest:started:2",
            run_id="run-duplicate-start",
            event_type="stage_started",
            stage="ingest",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME + timedelta(seconds=2),
        )

    append_pipeline_event(
        session,
        event_key="run-duplicate-start:ingest:bbc:started:1",
        run_id="run-duplicate-start",
        event_type="source_started",
        stage="ingest",
        source="bbc_arabic",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=2),
    )
    with pytest.raises(InvalidEventTransition, match="already has a start event"):
        append_pipeline_event(
            session,
            event_key="run-duplicate-start:ingest:bbc:started:2",
            run_id="run-duplicate-start",
            event_type="source_started",
            stage="ingest",
            source="bbc_arabic",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME + timedelta(seconds=3),
        )

    with pytest.raises(InvalidEventTransition, match="has not started"):
        append_pipeline_event(
            session,
            event_key="run-duplicate-start:process:cnn:started",
            run_id="run-duplicate-start",
            event_type="source_started",
            stage="process",
            source="cnn_arabic",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME + timedelta(seconds=3),
        )


def test_source_summary_distinguishes_zero_inserted_from_zero_yield(session):  # noqa: ANN001
    _start_run(session, "run-source")
    append_pipeline_event(
        session,
        event_key="run-source:ingest:started",
        run_id="run-source",
        event_type="stage_started",
        stage="ingest",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=1),
    )
    append_pipeline_event(
        session,
        event_key="run-source:ingest:bbc:started",
        run_id="run-source",
        event_type="source_started",
        stage="ingest",
        source="bbc_arabic",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=2),
    )
    terminal = append_pipeline_event(
        session,
        event_key="run-source:ingest:bbc:succeeded",
        run_id="run-source",
        event_type="source_succeeded",
        stage="ingest",
        source="bbc_arabic",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=5),
        attempt_count=10,
        output_count=4,
        inserted_count=0,
        error_count=0,
        selector_failure_count=0,
        parsing_failure_count=0,
        latest_successful_article_at=BASE_TIME + timedelta(seconds=4),
    )

    assert terminal.output_count == 4
    assert terminal.inserted_count == 0
    assert terminal.reason_code is None

    with pytest.raises(EventValidationError, match="zero-yield source"):
        append_pipeline_event(
            session,
            event_key="run-source:ingest:cnn:bad-zero",
            run_id="run-source",
            event_type="source_succeeded",
            stage="ingest",
            source="cnn_arabic",
            commit_sha="0123456789abcdef",
            occurred_at=BASE_TIME + timedelta(seconds=6),
            attempt_count=1,
            output_count=0,
            inserted_count=0,
            error_count=0,
            selector_failure_count=0,
            parsing_failure_count=0,
        )


def test_completed_fetch_failure_can_be_a_zero_yield_source_warning(session):  # noqa: ANN001
    _start_run(session, "run-fetch-warning")
    append_pipeline_event(
        session,
        event_key="run-fetch-warning:ingest:started",
        run_id="run-fetch-warning",
        event_type="stage_started",
        stage="ingest",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=1),
    )
    append_pipeline_event(
        session,
        event_key="run-fetch-warning:ingest:alarabiya:started",
        run_id="run-fetch-warning",
        event_type="source_started",
        stage="ingest",
        source="alarabiya",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=2),
    )

    terminal = append_pipeline_event(
        session,
        event_key="run-fetch-warning:ingest:alarabiya:succeeded",
        run_id="run-fetch-warning",
        event_type="source_succeeded",
        stage="ingest",
        source="alarabiya",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=3),
        attempt_count=3,
        output_count=0,
        inserted_count=0,
        error_count=3,
        selector_failure_count=0,
        parsing_failure_count=0,
        reason_code=PipelineReasonCode.SOURCE_FETCH_FAILED,
    )

    assert terminal.event_type == PipelineEventType.SOURCE_SUCCEEDED.value
    assert terminal.reason_code == PipelineReasonCode.SOURCE_FETCH_FAILED.value


def test_source_success_keeps_missing_publication_time_unknown(session):  # noqa: ANN001
    _start_run(session, "run-undated-source")
    append_pipeline_event(
        session,
        event_key="run-undated-source:ingest:started",
        run_id="run-undated-source",
        event_type="stage_started",
        stage="ingest",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=1),
    )
    append_pipeline_event(
        session,
        event_key="run-undated-source:ingest:source:started",
        run_id="run-undated-source",
        event_type="source_started",
        stage="ingest",
        source="undated_source",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=2),
    )

    terminal = append_pipeline_event(
        session,
        event_key="run-undated-source:ingest:source:succeeded",
        run_id="run-undated-source",
        event_type="source_succeeded",
        stage="ingest",
        source="undated_source",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=3),
        attempt_count=2,
        output_count=2,
        inserted_count=2,
        error_count=0,
        selector_failure_count=0,
        parsing_failure_count=0,
        latest_successful_article_at=None,
    )

    assert terminal.latest_successful_article_at is None


def test_sqlalchemy_rejects_update_and_delete_even_on_sqlite(session):  # noqa: ANN001
    row = _start_run(session, "run-immutable")
    session.commit()
    original_commit = row.commit_sha
    row_id = row.id

    row.commit_sha = "rewritten-history"
    with pytest.raises(AppendOnlyEventError, match="append-only"):
        session.flush()
    session.rollback()

    stored = session.get(PipelineEventORM, row_id)
    assert stored is not None
    assert stored.commit_sha == original_commit
    session.delete(stored)
    with pytest.raises(AppendOnlyEventError, match="cannot be deleted"):
        session.flush()


def test_monitor_appends_abandoned_only_after_latest_lease_expires(session):  # noqa: ANN001
    _start_run(session, "run-expired", lease_seconds=5)
    _start_run(session, "run-active", lease_seconds=30)
    _start_run(session, "run-heartbeat", lease_seconds=5)
    append_pipeline_event(
        session,
        event_key="run-heartbeat:heartbeat:1",
        run_id="run-heartbeat",
        event_type="run_heartbeat",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=4),
        lease_expires_at=BASE_TIME + timedelta(seconds=40),
    )
    _start_run(session, "run-finished", lease_seconds=5)
    append_pipeline_event(
        session,
        event_key="run-finished:succeeded",
        run_id="run-finished",
        event_type="run_succeeded",
        commit_sha="0123456789abcdef",
        occurred_at=BASE_TIME + timedelta(seconds=3),
    )

    abandoned = abandon_expired_runs(
        session,
        now=BASE_TIME + timedelta(seconds=10),
        monitor_commit_sha="fedcba9876543210",
    )

    assert [row.run_id for row in abandoned] == ["run-expired"]
    assert abandoned[0].reason_code == PipelineReasonCode.LEASE_EXPIRED.value
    assert abandon_expired_runs(
        session,
        now=BASE_TIME + timedelta(seconds=10),
        monitor_commit_sha="fedcba9876543210",
    ) == []
