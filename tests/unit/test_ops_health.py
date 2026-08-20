"""Health is rebuilt from event history and fails closed on partial runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.ops.events import PipelineEventType, PipelineReasonCode
from src.ops.health import load_run_health
from src.ops.ledger import abandon_expired_runs, append_pipeline_event


BASE_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
COMMIT_SHA = "0123456789abcdef"


def _append(session, run_id: str, key: str, event_type, seconds: int, **values):  # noqa: ANN001
    return append_pipeline_event(
        session,
        event_key=f"{run_id}:{key}",
        run_id=run_id,
        event_type=event_type,
        commit_sha=COMMIT_SHA,
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        **values,
    )


def _start(session, run_id: str, *, lease_seconds: int = 60):  # noqa: ANN001
    return _append(
        session,
        run_id,
        "started",
        PipelineEventType.RUN_STARTED,
        0,
        lease_expires_at=BASE_TIME + timedelta(seconds=lease_seconds),
        extractor_versions={"classifier": "2.0.1"},
    )


def _start_ingest_source(session, run_id: str, source: str = "bbc_arabic"):  # noqa: ANN001
    _append(
        session,
        run_id,
        "ingest-started",
        PipelineEventType.STAGE_STARTED,
        1,
        stage="ingest",
        input_count=10,
    )
    _append(
        session,
        run_id,
        f"ingest-{source}-started",
        PipelineEventType.SOURCE_STARTED,
        2,
        stage="ingest",
        source=source,
    )


def test_successful_run_exposes_stage_durations_and_source_counts(session):  # noqa: ANN001
    run_id = "run-healthy"
    _start(session, run_id)
    _start_ingest_source(session, run_id)
    _append(
        session,
        run_id,
        "ingest-bbc-succeeded",
        PipelineEventType.SOURCE_SUCCEEDED,
        5,
        stage="ingest",
        source="bbc_arabic",
        attempt_count=10,
        output_count=4,
        inserted_count=0,
        error_count=0,
        selector_failure_count=0,
        parsing_failure_count=0,
        latest_successful_article_at=BASE_TIME + timedelta(seconds=4),
    )
    _append(
        session,
        run_id,
        "ingest-succeeded",
        PipelineEventType.STAGE_SUCCEEDED,
        6,
        stage="ingest",
        input_count=10,
        output_count=4,
        error_count=0,
    )
    _append(
        session,
        run_id,
        "succeeded",
        PipelineEventType.RUN_SUCCEEDED,
        7,
    )

    health = load_run_health(
        session,
        run_id,
        now=BASE_TIME + timedelta(seconds=8),
        stale_after=timedelta(hours=1),
    )

    assert health.status == "healthy"
    assert health.duration_seconds == 7.0
    assert health.extractor_versions == {"classifier": "2.0.1"}
    assert health.stages[0].status == "succeeded"
    assert health.stages[0].duration_seconds == 5.0
    assert health.stages[0].output_count == 4
    assert health.sources[0].status == "healthy"
    assert health.sources[0].yielded_count == 4
    # Zero inserts can mean every fetched article was already known.  It is
    # not the same as a source yielding no articles.
    assert health.sources[0].inserted_count == 0


def test_zero_yield_source_is_degraded_with_only_a_safe_message(session):  # noqa: ANN001
    run_id = "run-zero-yield"
    _start(session, run_id)
    _start_ingest_source(session, run_id)
    _append(
        session,
        run_id,
        "ingest-bbc-zero",
        PipelineEventType.SOURCE_SUCCEEDED,
        5,
        stage="ingest",
        source="bbc_arabic",
        attempt_count=1,
        output_count=0,
        inserted_count=0,
        error_count=0,
        selector_failure_count=0,
        parsing_failure_count=0,
        reason_code=PipelineReasonCode.SOURCE_ZERO_YIELD,
    )
    _append(
        session,
        run_id,
        "ingest-succeeded",
        PipelineEventType.STAGE_SUCCEEDED,
        6,
        stage="ingest",
        output_count=0,
        error_count=0,
    )
    _append(
        session,
        run_id,
        "succeeded",
        PipelineEventType.RUN_SUCCEEDED,
        7,
    )

    health = load_run_health(session, run_id, now=BASE_TIME + timedelta(seconds=8))
    serialized = health.to_dict()

    assert health.status == "degraded"
    assert health.sources[0].status == "degraded"
    assert serialized["reason"] == {
        "code": "source_zero_yield",
        "message": "The source completed without producing an article.",
    }
    assert "traceback" not in str(serialized).lower()
    assert "password" not in str(serialized).lower()


def test_broken_source_and_stage_make_run_failed_without_private_error_text(session):  # noqa: ANN001
    run_id = "run-broken-source"
    _start(session, run_id)
    _start_ingest_source(session, run_id)
    _append(
        session,
        run_id,
        "ingest-bbc-failed",
        PipelineEventType.SOURCE_FAILED,
        4,
        stage="ingest",
        source="bbc_arabic",
        attempt_count=1,
        output_count=0,
        inserted_count=0,
        error_count=1,
        selector_failure_count=1,
        parsing_failure_count=0,
        reason_code=PipelineReasonCode.SOURCE_SELECTOR_FAILED,
    )
    _append(
        session,
        run_id,
        "ingest-failed",
        PipelineEventType.STAGE_FAILED,
        5,
        stage="ingest",
        output_count=0,
        error_count=1,
        reason_code=PipelineReasonCode.UPSTREAM_STAGE_FAILED,
    )
    _append(
        session,
        run_id,
        "failed",
        PipelineEventType.RUN_FAILED,
        6,
        reason_code=PipelineReasonCode.UPSTREAM_STAGE_FAILED,
    )

    health = load_run_health(session, run_id, now=BASE_TIME + timedelta(seconds=7))

    assert health.status == "failed"
    assert health.sources[0].status == "failed"
    assert health.sources[0].reason is not None
    assert health.sources[0].reason.code == "source_selector_failed"
    assert health.sources[0].reason.message == (
        "The source page did not match its configured selectors."
    )


def test_partial_or_overdue_run_can_never_look_healthy(session):  # noqa: ANN001
    run_id = "run-partial"
    _start(session, run_id, lease_seconds=10)
    _append(
        session,
        run_id,
        "process-started",
        PipelineEventType.STAGE_STARTED,
        1,
        stage="process",
    )

    running = load_run_health(session, run_id, now=BASE_TIME + timedelta(seconds=5))
    overdue = load_run_health(session, run_id, now=BASE_TIME + timedelta(seconds=11))

    assert running.status == "running"
    assert running.stages[0].status == "running"
    assert overdue.status == "overdue"
    assert overdue.reason is not None
    assert overdue.reason.code == "lease_expired"


def test_monitor_turns_a_killed_run_into_an_audited_abandonment(session):  # noqa: ANN001
    run_id = "run-killed"
    _start(session, run_id, lease_seconds=5)
    _append(
        session,
        run_id,
        "extract-started",
        PipelineEventType.STAGE_STARTED,
        1,
        stage="extract",
    )

    abandon_expired_runs(
        session,
        now=BASE_TIME + timedelta(seconds=6),
        monitor_commit_sha="fedcba9876543210",
    )
    health = load_run_health(session, run_id, now=BASE_TIME + timedelta(seconds=6))

    assert health.status == "abandoned"
    assert health.reason is not None
    assert health.reason.code == "lease_expired"
    assert health.stages[0].status == "abandoned"


def test_stale_latest_article_degrades_an_otherwise_successful_source(session):  # noqa: ANN001
    run_id = "run-stale-source"
    _start(session, run_id)
    _start_ingest_source(session, run_id)
    _append(
        session,
        run_id,
        "ingest-bbc-succeeded",
        PipelineEventType.SOURCE_SUCCEEDED,
        5,
        stage="ingest",
        source="bbc_arabic",
        attempt_count=5,
        output_count=2,
        inserted_count=2,
        error_count=0,
        selector_failure_count=0,
        parsing_failure_count=0,
        latest_successful_article_at=BASE_TIME - timedelta(days=2),
    )
    _append(
        session,
        run_id,
        "ingest-succeeded",
        PipelineEventType.STAGE_SUCCEEDED,
        6,
        stage="ingest",
        output_count=2,
        error_count=0,
    )
    _append(
        session,
        run_id,
        "succeeded",
        PipelineEventType.RUN_SUCCEEDED,
        7,
    )

    health = load_run_health(
        session,
        run_id,
        now=BASE_TIME + timedelta(seconds=8),
        stale_after=timedelta(hours=24),
    )

    assert health.status == "degraded"
    assert health.sources[0].reason is not None
    assert health.sources[0].reason.code == "data_stale"


def test_terminal_success_with_an_unfinished_stage_is_partial_not_healthy(session):  # noqa: ANN001
    run_id = "run-inconsistent-success"
    _start(session, run_id)
    _append(
        session,
        run_id,
        "process-started",
        PipelineEventType.STAGE_STARTED,
        1,
        stage="process",
    )
    _append(
        session,
        run_id,
        "succeeded",
        PipelineEventType.RUN_SUCCEEDED,
        2,
    )

    health = load_run_health(session, run_id, now=BASE_TIME + timedelta(seconds=3))

    assert health.status == "partial"
    assert health.stages[0].status == "partial"
