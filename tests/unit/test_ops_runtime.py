"""Regression coverage for dormant ledger-backed pipeline orchestration."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import src.ops.runtime as runtime
from src.ops.events import PipelineEventType, PipelineReasonCode
from src.ops.runtime import (
    PipelineExecutionFailed,
    PipelineStage,
    RuntimeConfigurationError,
    SourceOutcome,
    StageOutcome,
    bake_stage,
    build_core_pipeline_stages,
    core_component_versions,
    outcome_from_ingest_stats,
    run_core_pipeline,
    run_orchestrated_pipeline,
)
from src.ops.releases import ReleaseArtifact, materialize_release_candidate
from src.store.orm import PipelineEventORM


BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
COMMIT_SHA = "0123456789abcdef"
SECRET_TEXT = "r2_secret=private-credential response_body=not-public"


def _scope(session):  # noqa: ANN001
    return lambda: nullcontext(session)


def _clock() -> datetime:
    # The recorder itself advances equal clock readings by microseconds.  This
    # makes event ordering deterministic without sleeping in a unit test.
    return BASE_TIME


def _source(
    *,
    status: str = "success",
    yielded: int = 2,
    reason: PipelineReasonCode | None = None,
) -> SourceOutcome:
    return SourceOutcome(
        name="BBC Arabic",
        status=status,
        attempt_count=3,
        yielded_count=yielded,
        inserted_count=1 if yielded else 0,
        error_count=0 if status != "failed" else 1,
        selector_failure_count=0,
        parsing_failure_count=0,
        latest_successful_article_at=BASE_TIME if yielded else None,
        reason_code=reason,
    )


def _event_types(session, run_id: str) -> list[str]:  # noqa: ANN001
    return list(
        session.scalars(
            select(PipelineEventORM.event_type)
            .where(PipelineEventORM.run_id == run_id)
            .order_by(PipelineEventORM.id.asc())
        )
    )


_RELEASE_EVENT_TYPES = {
    PipelineEventType.RELEASE_RESERVED.value,
    PipelineEventType.RELEASE_CANDIDATE_CREATED.value,
    PipelineEventType.PROMOTION_STARTED.value,
    PipelineEventType.RELEASE_PUBLISHED.value,
    PipelineEventType.RELEASE_FAILED.value,
    PipelineEventType.RELEASE_SUPERSEDED.value,
}


def test_runtime_records_complete_stage_and_source_history_then_prepares_candidate(
    session, blob_store
):
    run_id = "runtime-healthy"

    def ingest(runtime) -> StageOutcome:  # noqa: ANN001
        runtime.source_started("BBC Arabic")
        runtime.heartbeat()
        runtime.source_completed(_source())
        return StageOutcome(
            input_count=2,
            output_count=1,
            sources=(_source(),),
        )

    stages = (
        PipelineStage(
            name="ingest",
            source_names=("BBC Arabic",),
            operation=ingest,
        ),
        PipelineStage(
            name="process",
            operation=lambda _runtime: StageOutcome(input_count=1, output_count=1),
        ),
        PipelineStage(
            name="extract",
            operation=lambda _runtime: StageOutcome(input_count=1, output_count=1),
        ),
        PipelineStage(
            name="resolve",
            operation=lambda _runtime: StageOutcome(input_count=1, output_count=1),
        ),
        PipelineStage(
            name="bake",
            operation=lambda _runtime: StageOutcome(
                input_count=1,
                output_count=1,
                artifacts=(
                    # A candidate contains a snapshot byte payload, but this
                    # test intentionally has no deployment adapter at all.
                    ReleaseArtifact(
                        "data.json", b'{"schema_version":"1.0.0"}\n', "application/json"
                    ),
                ),
            ),
        ),
    )

    result = run_orchestrated_pipeline(
        stages,
        session_scope=_scope(session),
        run_id=run_id,
        commit_sha=COMMIT_SHA,
        clock=_clock,
        prepare_release=True,
        release_id="runtime-healthy-release",
        blob_store=blob_store,
    )

    assert result.health.status == "healthy"
    assert result.release_candidate is not None
    assert result.release_candidate.release_id == "runtime-healthy-release"
    assert result.health.sources[0].name == "BBC Arabic"
    assert result.health.sources[0].yielded_count == 2
    assert result.health.sources[0].duration_seconds is not None
    assert result.health.sources[0].duration_seconds > 0
    event_types = _event_types(session, run_id)
    assert event_types[0] == PipelineEventType.RUN_STARTED.value
    assert PipelineEventType.RUN_HEARTBEAT.value in event_types
    assert PipelineEventType.SOURCE_STARTED.value in event_types
    assert PipelineEventType.SOURCE_SUCCEEDED.value in event_types
    assert PipelineEventType.RELEASE_RESERVED.value in event_types
    assert PipelineEventType.RELEASE_CANDIDATE_CREATED.value in event_types
    assert event_types[-1] == PipelineEventType.RUN_SUCCEEDED.value
    # Candidate creation is not publication.  The runtime module does not
    # import a publisher and it must not fabricate a live-release event.
    assert PipelineEventType.PROMOTION_STARTED.value not in event_types
    assert PipelineEventType.RELEASE_PUBLISHED.value not in event_types


def test_bake_candidate_carries_the_exact_static_dashboard_shell(
    session, blob_store, monkeypatch, tmp_path
):
    """The candidate must match the three files the current workflow deploys."""
    from scripts import bake_dashboard_data

    out_path = tmp_path / "dist" / "data.json"

    def fake_run_bake(path: Path, recent_limit: int = 30):  # noqa: ANN001
        del recent_limit
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"stats":{"total_processed":1}}\n')
        countries = path.parent / "countries"
        countries.mkdir()
        (countries / "lebanon.json").write_bytes(b'{"country":"Lebanon"}\n')
        return {"stats": {"total_processed": 1}}

    monkeypatch.setattr(bake_dashboard_data, "run_bake", fake_run_bake)

    result = run_orchestrated_pipeline(
        (bake_stage(out_path),),
        session_scope=_scope(session),
        run_id="runtime-static-shell",
        commit_sha=COMMIT_SHA,
        clock=_clock,
        prepare_release=True,
        release_id="runtime-static-shell-release",
        blob_store=blob_store,
    )

    assert result.release_candidate is not None
    artifacts = {
        artifact.path: artifact.data
        for artifact in materialize_release_candidate(blob_store, result.release_candidate)
    }
    repo_root = Path(__file__).resolve().parents[2]
    assert artifacts["data.json"] == b'{"stats":{"total_processed":1}}\n'
    assert artifacts["countries/lebanon.json"] == b'{"country":"Lebanon"}\n'
    assert artifacts["index.html"] == (
        repo_root / "src" / "api" / "static" / "dashboard.html"
    ).read_bytes()
    assert artifacts["country.html"] == (
        repo_root / "src" / "api" / "static" / "country.html"
    ).read_bytes()


def test_runtime_turns_a_stage_exception_into_safe_stage_and_run_terminals(session):
    run_id = "runtime-stage-exception"

    def boom(_runtime) -> StageOutcome:  # noqa: ANN001
        raise RuntimeError(SECRET_TEXT)

    with pytest.raises(PipelineExecutionFailed) as raised:
        run_orchestrated_pipeline(
            (PipelineStage(name="process", operation=boom),),
            session_scope=_scope(session),
            run_id=run_id,
            commit_sha=COMMIT_SHA,
            clock=_clock,
        )

    error = raised.value
    assert error.reason_code == PipelineReasonCode.UNEXPECTED_ERROR
    assert error.health.status == "failed"
    assert error.health.stages[0].status == "failed"
    assert SECRET_TEXT not in str(error)
    assert SECRET_TEXT not in str(error.health.to_dict())
    rows = list(
        session.scalars(
            select(PipelineEventORM)
            .where(PipelineEventORM.run_id == run_id)
            .order_by(PipelineEventORM.id.asc())
        )
    )
    assert rows[-1].event_type == PipelineEventType.RUN_FAILED.value
    assert rows[-1].reason_code == PipelineReasonCode.UNEXPECTED_ERROR.value
    assert all(SECRET_TEXT not in str(row.__dict__) for row in rows)


def test_failed_source_closes_source_stage_and_run_without_candidate(session, blob_store):
    run_id = "runtime-source-failure"
    stages = (
        PipelineStage(
            name="ingest",
            source_names=("BBC Arabic",),
            operation=lambda _runtime: StageOutcome(
                input_count=1,
                output_count=0,
                error_count=1,
                sources=(
                    _source(
                        status="failed",
                        yielded=0,
                        reason=PipelineReasonCode.SOURCE_FETCH_FAILED,
                    ),
                ),
                # The stage may have local bytes, but a failed source means
                # they cannot become a release candidate.
                artifacts=(
                    ReleaseArtifact(
                        "data.json", b"{}\n", "application/json"
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(PipelineExecutionFailed) as raised:
        run_orchestrated_pipeline(
            stages,
            session_scope=_scope(session),
            run_id=run_id,
            commit_sha=COMMIT_SHA,
            clock=_clock,
            prepare_release=True,
            release_id="source-failure-release",
            blob_store=blob_store,
        )

    health = raised.value.health
    assert health.status == "failed"
    assert health.stages[0].status == "failed"
    assert health.sources[0].status == "failed"
    assert health.sources[0].reason is not None
    assert health.sources[0].reason.code == PipelineReasonCode.SOURCE_FETCH_FAILED.value
    event_types = _event_types(session, run_id)
    assert PipelineEventType.SOURCE_FAILED.value in event_types
    assert PipelineEventType.STAGE_FAILED.value in event_types
    assert PipelineEventType.RUN_FAILED.value in event_types
    assert PipelineEventType.RELEASE_RESERVED.value not in event_types
    assert PipelineEventType.RELEASE_CANDIDATE_CREATED.value not in event_types


def test_degraded_source_blocks_candidate_and_records_prepare_stage_terminal(
    session, blob_store
):
    run_id = "runtime-degraded-release"
    stages = (
        PipelineStage(
            name="ingest",
            source_names=("BBC Arabic",),
            operation=lambda _runtime: StageOutcome(
                input_count=0,
                output_count=0,
                sources=(
                    _source(
                        status="degraded",
                        yielded=0,
                        reason=PipelineReasonCode.SOURCE_ZERO_YIELD,
                    ),
                ),
            ),
        ),
        PipelineStage(
            name="bake",
            operation=lambda _runtime: StageOutcome(
                input_count=0,
                output_count=1,
                artifacts=(
                    ReleaseArtifact(
                        "data.json", b"{}\n", "application/json"
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(PipelineExecutionFailed) as raised:
        run_orchestrated_pipeline(
            stages,
            session_scope=_scope(session),
            run_id=run_id,
            commit_sha=COMMIT_SHA,
            clock=_clock,
            prepare_release=True,
            release_id="degraded-release",
            blob_store=blob_store,
        )

    health = raised.value.health
    assert health.status == "failed"
    assert any(stage.name == "prepare_release" and stage.status == "failed" for stage in health.stages)
    assert health.sources[0].status == "degraded"
    event_types = _event_types(session, run_id)
    assert PipelineEventType.RUN_SUCCEEDED.value not in event_types
    assert PipelineEventType.RELEASE_RESERVED.value not in event_types
    assert PipelineEventType.RELEASE_PUBLISHED.value not in event_types


def test_ingest_adapter_keeps_duplicate_yield_distinct_from_insertions():
    class IngestStats:
        attempted = 3
        inserted = 0
        sources = {
            "BBC Arabic": {
                "status": "success",
                "article_attempt_count": 3,
                "article_yield_count": 3,
                "inserted_count": 0,
                "error_count": 0,
                "selector_failure_count": 0,
                "parsing_failure_count": 0,
                "latest_successful_article_at": BASE_TIME,
                "reason_code": None,
            }
        }

    outcome = outcome_from_ingest_stats(IngestStats())

    assert outcome.input_count == 3
    assert outcome.output_count == 0
    assert outcome.sources[0].yielded_count == 3
    assert outcome.sources[0].inserted_count == 0
    assert outcome.sources[0].status == "success"
    assert outcome.fatal_reason_code is None


def test_inconsistent_zero_yield_reason_fails_closed_before_ledger_write():
    class IngestStats:
        attempted = 1
        inserted = 1
        sources = {
            "BBC Arabic": {
                "status": "success",
                "article_attempt_count": 1,
                "article_yield_count": 1,
                "inserted_count": 1,
                "error_count": 0,
                "selector_failure_count": 0,
                "parsing_failure_count": 0,
                "reason_code": PipelineReasonCode.SOURCE_ZERO_YIELD.value,
            }
        }

    outcome = outcome_from_ingest_stats(IngestStats())

    assert outcome.sources[0].status == "failed"
    assert outcome.sources[0].reason_code == PipelineReasonCode.UNEXPECTED_ERROR


def test_malformed_zero_yield_source_telemetry_fails_closed_before_ledger_write():
    class IngestStats:
        attempted = 0
        inserted = 0
        sources = {
            "BBC Arabic": {
                "status": "degraded",
                "article_attempt_count": 0,
                "article_yield_count": 0,
                "inserted_count": 0,
                "error_count": 1,
                "selector_failure_count": 0,
                "parsing_failure_count": 0,
                "reason_code": "not-a-safe-reason",
            }
        }

    outcome = outcome_from_ingest_stats(IngestStats())

    assert outcome.sources[0].status == "failed"
    assert outcome.sources[0].reason_code == PipelineReasonCode.UNEXPECTED_ERROR
    assert outcome.fatal_reason_code == PipelineReasonCode.SOURCE_ZERO_YIELD


def test_core_wrapper_refuses_candidate_mode_without_a_baked_bundle():
    with pytest.raises(RuntimeConfigurationError, match="requires bake_out_path"):
        run_core_pipeline(prepare_release=True)


def test_hard_source_failure_blocks_bake_and_never_emits_a_release_event(session):
    """A failed source is terminal for the data pipeline, before baking."""
    baked = False

    def should_not_bake(_runtime) -> StageOutcome:  # noqa: ANN001
        nonlocal baked
        baked = True
        return StageOutcome(output_count=1)

    with pytest.raises(PipelineExecutionFailed) as raised:
        run_orchestrated_pipeline(
            (
                PipelineStage(
                    name="ingest",
                    source_names=("BBC Arabic",),
                    operation=lambda _runtime: StageOutcome(
                        input_count=1,
                        error_count=1,
                        sources=(
                            _source(
                                status="failed",
                                yielded=0,
                                reason=PipelineReasonCode.SOURCE_FETCH_FAILED,
                            ),
                        ),
                    ),
                ),
                PipelineStage(name="bake", operation=should_not_bake),
            ),
            session_scope=_scope(session),
            run_id="ledger-hard-source-failure",
            commit_sha=COMMIT_SHA,
            clock=_clock,
            prepare_release=False,
        )

    assert raised.value.health.status == "failed"
    assert baked is False
    event_types = _event_types(session, "ledger-hard-source-failure")
    assert PipelineEventType.RUN_FAILED.value in event_types
    assert PipelineEventType.STAGE_STARTED.value in event_types
    assert _RELEASE_EVENT_TYPES.isdisjoint(event_types)


def test_completed_fetch_failure_with_other_source_coverage_bakes_degraded(
    session, monkeypatch, tmp_path
):
    """A temporary source block must not suppress a covered snapshot."""
    from scripts import bake_dashboard_data
    from src.pipeline import extract_core, ingest_core, process_core, resolve_core

    execution: list[str] = []
    out_path = tmp_path / "dist" / "data.json"
    source_rows = {
        "AlArabiya": {
            "status": "degraded",
            "article_attempt_count": 0,
            "article_yield_count": 0,
            "inserted_count": 0,
            "error_count": 3,
            "selector_failure_count": 0,
            "parsing_failure_count": 0,
            "reason_code": PipelineReasonCode.SOURCE_FETCH_FAILED.value,
        },
        "BBC Arabic": {
            "status": "success",
            "article_attempt_count": 1,
            "article_yield_count": 1,
            "inserted_count": 0,
            "error_count": 0,
            "selector_failure_count": 0,
            "parsing_failure_count": 0,
            "latest_successful_article_at": BASE_TIME,
            "reason_code": None,
        },
    }

    def fake_ingest(**kwargs):  # noqa: ANN001
        execution.append("ingest")
        for name, telemetry in source_rows.items():
            kwargs["on_source_started"](name)
            kwargs["on_source_finished"](name, telemetry)
        return SimpleNamespace(attempted=1, inserted=0, sources=source_rows)

    def fake_process():
        execution.append("process")
        return SimpleNamespace(scanned=0, processed=0, errors=0)

    def fake_extract():
        execution.append("extract")
        return SimpleNamespace(documents_scanned=0, documents_processed=0, errors=0)

    def fake_resolve(**kwargs):
        del kwargs
        execution.append("resolve")
        return SimpleNamespace(mentions=0, entities_created=0)

    def fake_bake(path: Path, recent_limit: int = 30):  # noqa: ANN001
        del recent_limit
        execution.append("bake")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"stats":{"total_processed":0}}\n')
        return {"stats": {"total_processed": 0}}

    monkeypatch.setattr(
        runtime,
        "configured_ingest_source_names",
        lambda: ("AlArabiya", "BBC Arabic"),
    )
    monkeypatch.setattr(ingest_core, "run_core_ingestion", fake_ingest)
    monkeypatch.setattr(process_core, "run_core_processing", fake_process)
    monkeypatch.setattr(extract_core, "run_core_extraction", fake_extract)
    monkeypatch.setattr(resolve_core, "run_core_resolution", fake_resolve)
    monkeypatch.setattr(bake_dashboard_data, "run_bake", fake_bake)

    result = run_core_pipeline(
        bake_out_path=out_path,
        session_scope=_scope(session),
        run_id="covered-fetch-degraded",
        commit_sha=COMMIT_SHA,
        clock=_clock,
    )

    assert execution == ["ingest", "process", "extract", "resolve", "bake"]
    assert out_path.is_file()
    assert result.release_candidate is None
    assert result.health.status == "degraded"
    fetch_source = next(
        source for source in result.health.sources if source.name == "AlArabiya"
    )
    assert fetch_source.status == "degraded"
    assert fetch_source.reason is not None
    assert fetch_source.reason.code == PipelineReasonCode.SOURCE_FETCH_FAILED.value
    assert next(stage for stage in result.health.stages if stage.name == "bake").status == "succeeded"
    event_types = _event_types(session, result.run_id)
    assert PipelineEventType.SOURCE_FAILED.value not in event_types
    assert PipelineEventType.RUN_SUCCEEDED.value in event_types
    assert _RELEASE_EVENT_TYPES.isdisjoint(event_types)


def test_all_zero_sources_fail_closed_before_bake_even_with_known_warnings(
    session, monkeypatch, tmp_path
):
    """An empty run must never gain freshness merely because baking ran."""
    from src.pipeline import ingest_core

    execution: list[str] = []
    source_rows = {
        "AlArabiya": {
            "status": "degraded",
            "article_attempt_count": 0,
            "article_yield_count": 0,
            "inserted_count": 0,
            "error_count": 3,
            "selector_failure_count": 0,
            "parsing_failure_count": 0,
            "reason_code": PipelineReasonCode.SOURCE_FETCH_FAILED.value,
        },
        "BBC Arabic": {
            "status": "degraded",
            "article_attempt_count": 0,
            "article_yield_count": 0,
            "inserted_count": 0,
            "error_count": 1,
            "selector_failure_count": 1,
            "parsing_failure_count": 0,
            "reason_code": PipelineReasonCode.SOURCE_SELECTOR_FAILED.value,
        },
    }

    def fake_ingest(**kwargs):  # noqa: ANN001
        execution.append("ingest")
        for name, telemetry in source_rows.items():
            kwargs["on_source_started"](name)
            kwargs["on_source_finished"](name, telemetry)
        return SimpleNamespace(attempted=0, inserted=0, sources=source_rows)

    monkeypatch.setattr(
        runtime,
        "configured_ingest_source_names",
        lambda: ("AlArabiya", "BBC Arabic"),
    )
    monkeypatch.setattr(ingest_core, "run_core_ingestion", fake_ingest)

    with pytest.raises(PipelineExecutionFailed) as raised:
        run_core_pipeline(
            bake_out_path=tmp_path / "dist" / "data.json",
            session_scope=_scope(session),
            run_id="all-zero-coverage",
            commit_sha=COMMIT_SHA,
            clock=_clock,
        )

    assert execution == ["ingest"]
    assert raised.value.reason_code == PipelineReasonCode.SOURCE_ZERO_YIELD
    health = raised.value.health
    assert health.status == "failed"
    assert health.stages[0].name == "ingest"
    assert health.stages[0].status == "failed"
    assert {source.status for source in health.sources} == {"degraded"}
    assert {
        source.reason.code for source in health.sources if source.reason is not None
    } == {
        PipelineReasonCode.SOURCE_FETCH_FAILED.value,
        PipelineReasonCode.SOURCE_SELECTOR_FAILED.value,
    }
    event_types = _event_types(session, "all-zero-coverage")
    assert PipelineEventType.STAGE_FAILED.value in event_types
    assert PipelineEventType.RUN_FAILED.value in event_types
    assert PipelineEventType.RUN_SUCCEEDED.value not in event_types
    assert _RELEASE_EVENT_TYPES.isdisjoint(event_types)


def test_best_effort_translation_failure_bakes_and_finishes_degraded_without_release_events(
    session, monkeypatch, tmp_path
):
    """An optional provider outage must not hide a usable Arabic snapshot."""
    from scripts import bake_dashboard_data
    from src.pipeline import extract_core, ingest_core, process_core, resolve_core, translate_core

    execution: list[str] = []
    out_path = tmp_path / "dist" / "data.json"

    def fake_ingest(**_kwargs):  # noqa: ANN001
        execution.append("ingest")
        return SimpleNamespace(attempted=0, inserted=0, sources={})

    def fake_process():
        execution.append("process")
        return SimpleNamespace(scanned=0, processed=0, errors=0)

    def fake_extract():
        execution.append("extract")
        return SimpleNamespace(documents_scanned=0, documents_processed=0, errors=0)

    def fake_resolve(**kwargs):
        del kwargs
        execution.append("resolve")
        return SimpleNamespace(mentions=0, entities_created=0)

    def broken_translate():
        execution.append("translate")
        raise RuntimeError(SECRET_TEXT)

    def fake_bake(path: Path, recent_limit: int = 30):  # noqa: ANN001
        del recent_limit
        execution.append("bake")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"stats":{"total_processed":0}}\n')
        return {"stats": {"total_processed": 0}}

    monkeypatch.setattr(runtime, "configured_ingest_source_names", lambda: ())
    monkeypatch.setattr(ingest_core, "run_core_ingestion", fake_ingest)
    monkeypatch.setattr(process_core, "run_core_processing", fake_process)
    monkeypatch.setattr(extract_core, "run_core_extraction", fake_extract)
    monkeypatch.setattr(resolve_core, "run_core_resolution", fake_resolve)
    monkeypatch.setattr(translate_core, "run_core_translation", broken_translate)
    monkeypatch.setattr(bake_dashboard_data, "run_bake", fake_bake)

    result = run_orchestrated_pipeline(
        build_core_pipeline_stages(
            translation_mode="best-effort",
            bake_out_path=out_path,
        ),
        session_scope=_scope(session),
        run_id="ledger-translation-degraded",
        commit_sha=COMMIT_SHA,
        clock=_clock,
        prepare_release=False,
    )

    assert execution == ["ingest", "process", "extract", "resolve", "translate", "bake"]
    assert result.release_candidate is None
    assert result.health.status == "degraded"
    translate_stage = next(stage for stage in result.health.stages if stage.name == "translate")
    assert translate_stage.status == "degraded"
    assert translate_stage.error_count == 1
    assert next(stage for stage in result.health.stages if stage.name == "bake").status == "succeeded"
    event_types = _event_types(session, result.run_id)
    assert PipelineEventType.RUN_SUCCEEDED.value in event_types
    assert _RELEASE_EVENT_TYPES.isdisjoint(event_types)
    assert SECRET_TEXT not in str(result.health.to_dict())
    assert all(
        SECRET_TEXT not in str(row.__dict__)
        for row in session.scalars(
            select(PipelineEventORM).where(PipelineEventORM.run_id == result.run_id)
        )
    )


def test_core_component_versions_are_static_and_translation_mode_specific():
    skipped = core_component_versions(translation_mode="skip")
    translated = core_component_versions(translation_mode="best-effort")

    assert {
        "aljazeera_scraper",
        "bbc_arabic_scraper",
        "cnn_arabic_scraper",
        "rule_based_document_classifier",
        "gazetteer_extractor",
        "pair_scorer_resolver",
    }.issubset(skipped)
    assert "deepl_translator" not in skipped
    assert translated["deepl_translator"]
    assert {name: translated[name] for name in skipped} == skipped


def test_ledger_only_run_records_run_id_commit_and_component_versions_without_release_events(
    session,
):
    versions = core_component_versions(translation_mode="skip")
    run_id = "ledger-traceability"

    result = run_orchestrated_pipeline(
        (
            PipelineStage(
                name="process",
                operation=lambda _runtime: StageOutcome(input_count=3, output_count=3),
            ),
        ),
        session_scope=_scope(session),
        run_id=run_id,
        commit_sha=COMMIT_SHA,
        extractor_versions=versions,
        clock=_clock,
        prepare_release=False,
    )

    started = session.scalar(
        select(PipelineEventORM).where(
            PipelineEventORM.run_id == run_id,
            PipelineEventORM.event_type == PipelineEventType.RUN_STARTED.value,
        )
    )
    assert started is not None
    assert started.run_id == run_id
    assert started.commit_sha == COMMIT_SHA
    assert started.extractor_versions == versions
    assert result.health.extractor_versions == versions
    assert result.release_candidate is None
    assert _RELEASE_EVENT_TYPES.isdisjoint(_event_types(session, run_id))


def test_core_wrapper_wires_static_versions_and_never_enables_release_by_default(
    monkeypatch, tmp_path
):
    captured: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(runtime, "build_core_pipeline_stages", lambda **kwargs: (sentinel,))

    def capture_run(stages, **kwargs):  # noqa: ANN001
        captured["stages"] = stages
        captured.update(kwargs)
        return "completed"

    monkeypatch.setattr(runtime, "run_orchestrated_pipeline", capture_run)

    result = run_core_pipeline(
        translation_mode="best-effort",
        bake_out_path=tmp_path / "data.json",
        run_id="wrapper-ledger-only",
        commit_sha=COMMIT_SHA,
    )

    assert result == "completed"
    assert captured["stages"] == (sentinel,)
    assert captured["prepare_release"] is False
    assert captured["release_id"] is None
    assert captured["extractor_versions"] == core_component_versions(
        translation_mode="best-effort"
    )
