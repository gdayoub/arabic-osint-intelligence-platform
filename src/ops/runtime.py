"""Durable orchestration for the core OSINT data pipeline.

This module records data-work boundaries for the scheduled pipeline. It does
not claim that the external static-site deployment succeeded: candidate
preparation and promotion remain explicit, separate operations.

The useful split is small:

* existing pipeline functions do the domain work and return their counters;
* this module turns those counters into immutable run/stage/source facts;
* the optional release step creates an immutable candidate only.  It never
  imports a deployment adapter or calls a promotion function.

Keeping the side effects at this boundary means a killed runner still leaves a
truthful start/heartbeat trail, and a source exception never becomes public
error text by accident.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
from typing import Any, ContextManager, NoReturn
from uuid import uuid4

from sqlalchemy.orm import Session

from src.ops.events import PipelineEventType, PipelineReasonCode
from src.ops.health import RunHealth, load_run_health
from src.ops.ledger import append_pipeline_event
from src.ops.releases import CandidateIntegrityError, ReleaseArtifact, ReleaseCandidate, create_release_candidate
from src.store.blob import BlobStore, get_blob_store
from src.store.database import get_core_session


logger = logging.getLogger("ops.runtime")

SessionScopeFactory = Callable[[], ContextManager[Session]]
Clock = Callable[[], datetime]
StageOperation = Callable[["StageRuntime"], "StageOutcome"]

_SOURCE_ALIAS_PATTERN = re.compile(r"^[\w][\w .-]{0,99}$", re.UNICODE)
_STAGE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DEFAULT_LEASE = timedelta(minutes=30)
_SOURCE_WARNING_REASONS = {
    PipelineReasonCode.SOURCE_SELECTOR_FAILED,
    PipelineReasonCode.SOURCE_PARSE_FAILED,
    PipelineReasonCode.SOURCE_ZERO_YIELD,
    PipelineReasonCode.DATA_STALE,
}
_STATIC_RELEASE_ASSETS = (
    ("index.html", "dashboard.html"),
    ("country.html", "country.html"),
)
_TRANSLATION_MODES = frozenset({"skip", "best-effort"})


class RuntimeConfigurationError(ValueError):
    """Raised before an orchestration run can make an unsafe observation."""


class PipelineExecutionFailed(RuntimeError):
    """A safe, public-serializable description of a terminal runtime failure.

    The original exception remains chained for private runner diagnostics, but
    its text is deliberately absent from this exception and from the ledger.
    """

    def __init__(
        self,
        *,
        run_id: str,
        stage: str,
        reason_code: PipelineReasonCode,
        health: RunHealth,
    ) -> None:
        self.run_id = run_id
        self.stage = stage
        self.reason_code = reason_code
        self.health = health
        super().__init__(
            f"pipeline run {run_id!r} ended at {stage!r} with "
            f"{reason_code.value}"
        )


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Closed, per-source telemetry returned by an ingestion stage.

    ``status`` is deliberately limited to the three ways a completed source
    can look.  A source which never reaches this object is handled as a safe
    unexpected failure by the recorder; callers never pass exception text.
    """

    name: str
    status: str
    attempt_count: int
    yielded_count: int
    inserted_count: int
    error_count: int
    selector_failure_count: int
    parsing_failure_count: int
    latest_successful_article_at: datetime | None = None
    reason_code: PipelineReasonCode | None = None


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """Counters and optional release bytes produced by one completed stage."""

    input_count: int = 0
    output_count: int = 0
    error_count: int = 0
    sources: tuple[SourceOutcome, ...] = ()
    artifacts: tuple[ReleaseArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class StageRuntime:
    """Callbacks an operation may use while one stage is running.

    Existing operations can ignore this value.  Ingestion uses it so a source
    event starts immediately before that scraper runs, rather than when the
    whole ingestion batch begins.
    """

    _heartbeat: Callable[[], None]
    _source_started: Callable[[str], None]
    _source_completed: Callable[[SourceOutcome], None]

    def heartbeat(self) -> None:
        self._heartbeat()

    def source_started(self, source_name: str) -> None:
        self._source_started(source_name)

    def source_completed(self, outcome: SourceOutcome) -> None:
        self._source_completed(outcome)


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """One existing operation wrapped with stable operational identity."""

    name: str
    operation: StageOperation
    source_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrchestratedRun:
    """The durable result of one run, without any publication claim."""

    run_id: str
    commit_sha: str
    health: RunHealth
    release_candidate: ReleaseCandidate | None


def make_run_id(prefix: str = "pipeline") -> str:
    """Return a safe unique ID suitable for a manually triggered run."""
    _validate_stage_name(prefix, field="run prefix")
    return f"{prefix}-{uuid4().hex}"


def default_commit_sha() -> str:
    """Use the Actions SHA when available, with a clear local fallback."""
    candidate = os.environ.get("GITHUB_SHA", "local-unversioned").strip()
    if not candidate or len(candidate) > 64:
        return "local-unversioned"
    return candidate


def _as_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeConfigurationError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _nonnegative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeConfigurationError(f"{field} must be a non-negative integer")
    return value


def _validate_stage_name(value: str, *, field: str = "stage name") -> str:
    if not isinstance(value, str) or _STAGE_NAME_PATTERN.fullmatch(value) is None:
        raise RuntimeConfigurationError(
            f"{field} must use a lower-case stable identifier (letters, digits, _ or -)"
        )
    return value


def _validate_source_alias(value: str) -> str:
    """Accept a stable display alias, never a URL or arbitrary diagnostic."""
    if (
        not isinstance(value, str)
        or _SOURCE_ALIAS_PATTERN.fullmatch(value) is None
        or any(token in value for token in ("://", "@", "?", "#", "=", "&"))
    ):
        # Do not interpolate a bad value: it may itself contain a credential.
        raise RuntimeConfigurationError("source names must be stable non-URL aliases")
    return value


def _safe_reason(value: object, *, fallback: PipelineReasonCode) -> PipelineReasonCode:
    if value is None:
        return fallback
    try:
        return PipelineReasonCode(value)
    except (TypeError, ValueError):
        return fallback


def _event_key(run_id: str, kind: str, *parts: str) -> str:
    """Make ledger keys idempotent without exposing source values in them."""
    payload = "\x00".join((run_id, kind, *parts)).encode("utf-8")
    return f"runtime:{hashlib.sha256(payload).hexdigest()}"


def _source_outcome_from_ingest(name: str, raw: Mapping[str, Any]) -> SourceOutcome:
    """Adapt ``IngestCoreStats.sources`` without trusting arbitrary values.

    Ingestion already makes the source result safe, but this boundary is also
    used by custom scrapers.  Bad counters become an explicit failed source
    rather than a traceback or an invented success.
    """
    alias = _validate_source_alias(name)
    fields = {
        "attempt_count": raw.get("article_attempt_count", 0),
        "yielded_count": raw.get("article_yield_count", 0),
        "inserted_count": raw.get("inserted_count", 0),
        "error_count": raw.get("error_count", 0),
        "selector_failure_count": raw.get("selector_failure_count", 0),
        "parsing_failure_count": raw.get("parsing_failure_count", 0),
    }
    try:
        counters = {
            field_name: _nonnegative(value, field=f"source {field_name}")
            for field_name, value in fields.items()
        }
    except RuntimeConfigurationError:
        return SourceOutcome(
            name=alias,
            status="failed",
            attempt_count=0,
            yielded_count=0,
            inserted_count=0,
            error_count=1,
            selector_failure_count=0,
            parsing_failure_count=0,
            reason_code=PipelineReasonCode.UNEXPECTED_ERROR,
        )

    raw_status = raw.get("status")
    raw_reason = raw.get("reason_code")
    reason = _safe_reason(raw_reason, fallback=PipelineReasonCode.UNEXPECTED_ERROR)
    latest = raw.get("latest_successful_article_at")
    if latest is not None:
        try:
            latest = _as_utc(latest, field="latest_successful_article_at")
        except RuntimeConfigurationError:
            raw_status = "failed"
            reason = PipelineReasonCode.UNEXPECTED_ERROR
            latest = None

    if raw_status == "success":
        # A zero yield must carry the exact closed explanation the ledger
        # requires.  Positive yielded rows with a source warning are valid
        # degraded completion (for example one parse failure among many rows).
        if counters["yielded_count"] == 0:
            reason = PipelineReasonCode.SOURCE_ZERO_YIELD
            status = "degraded"
        elif raw_reason is None:
            reason = None
            status = "success"
        elif reason in _SOURCE_WARNING_REASONS:
            if reason == PipelineReasonCode.SOURCE_ZERO_YIELD:
                status = "failed"
                reason = PipelineReasonCode.UNEXPECTED_ERROR
            else:
                status = "degraded"
        else:
            status = "failed"
            reason = PipelineReasonCode.UNEXPECTED_ERROR
    elif raw_status == "degraded":
        if counters["yielded_count"] == 0:
            reason = PipelineReasonCode.SOURCE_ZERO_YIELD
        elif reason not in _SOURCE_WARNING_REASONS or reason == PipelineReasonCode.SOURCE_ZERO_YIELD:
            status = "failed"
            reason = PipelineReasonCode.UNEXPECTED_ERROR
        else:
            status = "degraded"
    elif raw_status == "failed":
        status = "failed"
        if raw_reason is None:
            reason = PipelineReasonCode.UNEXPECTED_ERROR
    else:
        status = "failed"
        reason = PipelineReasonCode.UNEXPECTED_ERROR

    return SourceOutcome(
        name=alias,
        status=status,
        latest_successful_article_at=latest,
        reason_code=reason,
        **counters,
    )


def outcome_from_ingest_stats(stats: Any) -> StageOutcome:
    """Adapt the current core ingestion result into event-safe counters."""
    raw_sources = getattr(stats, "sources", None)
    if not isinstance(raw_sources, Mapping):
        raise RuntimeConfigurationError("ingestion stats must expose a source mapping")
    if any(
        not isinstance(name, str) or not isinstance(raw, Mapping)
        for name, raw in raw_sources.items()
    ):
        raise RuntimeConfigurationError("ingestion source telemetry must use named mappings")
    sources = tuple(
        _source_outcome_from_ingest(name, raw)
        for name, raw in sorted(raw_sources.items())
    )
    return StageOutcome(
        input_count=_nonnegative(getattr(stats, "attempted", 0), field="ingest attempted"),
        output_count=_nonnegative(getattr(stats, "inserted", 0), field="ingest inserted"),
        error_count=sum(source.error_count for source in sources),
        sources=sources,
    )


def outcome_from_process_stats(stats: Any) -> StageOutcome:
    return StageOutcome(
        input_count=_nonnegative(getattr(stats, "scanned", 0), field="process scanned"),
        output_count=_nonnegative(getattr(stats, "processed", 0), field="process processed"),
        error_count=_nonnegative(getattr(stats, "errors", 0), field="process errors"),
    )


def outcome_from_extract_stats(stats: Any) -> StageOutcome:
    return StageOutcome(
        input_count=_nonnegative(
            getattr(stats, "documents_scanned", 0), field="extract documents scanned"
        ),
        output_count=_nonnegative(
            getattr(stats, "documents_processed", 0), field="extract documents processed"
        ),
        error_count=_nonnegative(getattr(stats, "errors", 0), field="extract errors"),
    )


def outcome_from_resolve_stats(stats: Any) -> StageOutcome:
    return StageOutcome(
        input_count=_nonnegative(getattr(stats, "mentions", 0), field="resolve mentions"),
        output_count=_nonnegative(
            getattr(stats, "entities_created", 0), field="resolve entities created"
        ),
    )


def outcome_from_translate_stats(stats: Any) -> StageOutcome:
    return StageOutcome(
        input_count=_nonnegative(getattr(stats, "titles_seen", 0), field="translate titles seen"),
        output_count=_nonnegative(
            getattr(stats, "newly_translated", 0), field="translate newly translated"
        ),
    )


def configured_ingest_source_names() -> tuple[str, ...]:
    """Read static scraper aliases before opening an ingestion run."""
    from src.pipeline.ingest_core import build_scrapers

    return tuple(_validate_source_alias(scraper.source_name) for scraper in build_scrapers())


def _validated_translation_mode(value: str) -> str:
    if value not in _TRANSLATION_MODES:
        choices = ", ".join(sorted(_TRANSLATION_MODES))
        raise RuntimeConfigurationError(f"translation_mode must be one of: {choices}")
    return value


def core_component_versions(*, translation_mode: str = "skip") -> dict[str, str]:
    """Return the exact code component versions used by one core run.

    The map is captured in ``run_started`` before source writes begin. It
    contains only stable component identities and versions, never settings,
    credentials, source URLs, or an unreviewed database value.
    """
    translation_mode = _validated_translation_mode(translation_mode)

    from src.extract.gazetteer import GazetteerExtractor
    from src.pipeline import process_core, resolve_core
    from src.pipeline.ingest_core import build_scrapers

    versions: dict[str, str] = {}
    for scraper in build_scrapers():
        name = scraper.NAME
        version = scraper.VERSION
        if name in versions and versions[name] != version:
            raise RuntimeConfigurationError("one component name cannot have two versions")
        versions[name] = version

    versions[process_core.EXTRACTOR_NAME] = process_core.EXTRACTOR_VERSION
    versions[GazetteerExtractor.name] = GazetteerExtractor.version
    versions[resolve_core.EXTRACTOR_NAME] = resolve_core.EXTRACTOR_VERSION

    if translation_mode == "best-effort":
        from src.processing.translation import EXTRACTOR_NAME, EXTRACTOR_VERSION

        versions[EXTRACTOR_NAME] = EXTRACTOR_VERSION

    return dict(sorted(versions.items()))


def dashboard_shell_artifacts(
    static_dir: Path | None = None,
) -> tuple[ReleaseArtifact, ...]:
    """Read the immutable HTML shell that the current Worker deploys.

    The existing workflow copies these exact source files into ``dist`` as
    ``index.html`` and ``country.html`` after baking the JSON data.  A release
    candidate must carry the same shell as its JSON or it is not a complete
    deployable snapshot.  This reads the files only; it never changes UI
    content or writes a public path.
    """
    directory = static_dir or (Path(__file__).resolve().parents[1] / "api" / "static")
    artifacts: list[ReleaseArtifact] = []
    for public_path, source_name in _STATIC_RELEASE_ASSETS:
        source_path = directory / source_name
        try:
            data = source_path.read_bytes()
        except OSError as exc:
            raise RuntimeConfigurationError(
                "required dashboard shell asset could not be read"
            ) from exc
        artifacts.append(
            ReleaseArtifact(
                path=public_path,
                data=data,
                content_type="text/html; charset=utf-8",
            )
        )
    return tuple(artifacts)


def bake_stage(
    out_path: Path,
    *,
    recent_limit: int = 30,
) -> PipelineStage:
    """Wrap the existing bake in a stage that returns every baked JSON file.

    This does not publish anything.  It only reads the locally staged bundle
    after ``run_bake`` has completed its existing all-or-nothing write.
    """
    out_path = Path(out_path)

    def execute(_runtime: StageRuntime) -> StageOutcome:
        from scripts.bake_dashboard_data import run_bake

        data = run_bake(out_path, recent_limit=recent_limit)
        if not out_path.is_file():
            raise RuntimeError("bake did not create its data snapshot")
        artifacts = [
            ReleaseArtifact(
                path="data.json",
                data=out_path.read_bytes(),
                content_type="application/json; charset=utf-8",
            ),
            *dashboard_shell_artifacts(),
        ]
        countries_dir = out_path.parent / "countries"
        if countries_dir.is_dir():
            for country_path in sorted(countries_dir.glob("*.json")):
                artifacts.append(
                    ReleaseArtifact(
                        path=f"countries/{country_path.name}",
                        data=country_path.read_bytes(),
                        content_type="application/json; charset=utf-8",
                    )
                )

        stats = data.get("stats", {}) if isinstance(data, Mapping) else {}
        processed = stats.get("total_processed", 0) if isinstance(stats, Mapping) else 0
        return StageOutcome(
            input_count=_nonnegative(processed, field="bake total processed"),
            output_count=len(artifacts),
            artifacts=tuple(artifacts),
        )

    return PipelineStage(name="bake", operation=execute)


def build_core_pipeline_stages(
    *,
    translation_mode: str = "skip",
    bake_out_path: Path | None = None,
) -> tuple[PipelineStage, ...]:
    """Build, but do not run, the current core pipeline in ledger order."""
    from src.pipeline.extract_core import run_core_extraction
    from src.pipeline.ingest_core import run_core_ingestion
    from src.pipeline.process_core import run_core_processing
    from src.pipeline.resolve_core import run_core_resolution

    translation_mode = _validated_translation_mode(translation_mode)

    def run_ingest(runtime: StageRuntime) -> StageOutcome:
        stats = run_core_ingestion(
            on_source_started=runtime.source_started,
            on_source_finished=lambda name, telemetry: runtime.source_completed(
                _source_outcome_from_ingest(name, telemetry)
            ),
        )
        return outcome_from_ingest_stats(stats)

    stages: list[PipelineStage] = [
        PipelineStage(
            name="ingest",
            source_names=configured_ingest_source_names(),
            operation=run_ingest,
        ),
        PipelineStage(
            name="process",
            operation=lambda _runtime: outcome_from_process_stats(run_core_processing()),
        ),
        PipelineStage(
            name="extract",
            operation=lambda _runtime: outcome_from_extract_stats(run_core_extraction()),
        ),
        PipelineStage(
            name="resolve",
            operation=lambda _runtime: outcome_from_resolve_stats(run_core_resolution()),
        ),
    ]
    if translation_mode == "best-effort":
        def run_translation(_runtime: StageRuntime) -> StageOutcome:
            """Keep the current optional DeepL behavior visible in health."""
            from src.pipeline.translate_core import run_core_translation

            try:
                return outcome_from_translate_stats(run_core_translation())
            except Exception:
                # The private job log retains the exception. The ledger gets
                # only an error count, so it accurately degrades health
                # without exposing a provider response or credential.
                logger.exception("Optional title translation failed")
                return StageOutcome(error_count=1)

        stages.append(
            PipelineStage(
                name="translate",
                operation=run_translation,
            )
        )
    if bake_out_path is not None:
        stages.append(bake_stage(bake_out_path))
    return tuple(stages)


@dataclass(slots=True)
class _RunRecorder:
    """Append events in durable, strictly ordered checkpoints."""

    session_scope: SessionScopeFactory
    run_id: str
    commit_sha: str
    lease_duration: timedelta
    clock: Clock
    extractor_versions: dict[str, str] = field(default_factory=dict)
    _last_occurred_at: datetime | None = None
    _heartbeat_index: int = 0

    def timestamp(self) -> datetime:
        value = _as_utc(self.clock(), field="clock result")
        if self._last_occurred_at is not None and value <= self._last_occurred_at:
            value = self._last_occurred_at + timedelta(microseconds=1)
        self._last_occurred_at = value
        return value

    def append(self, kind: str, event_type: PipelineEventType, **values: Any) -> None:
        occurred_at = self.timestamp()
        with self.session_scope() as session:
            append_pipeline_event(
                session,
                event_key=_event_key(self.run_id, kind),
                run_id=self.run_id,
                event_type=event_type,
                commit_sha=self.commit_sha,
                occurred_at=occurred_at,
                **values,
            )

    def start(self) -> None:
        occurred_at = self.timestamp()
        with self.session_scope() as session:
            append_pipeline_event(
                session,
                event_key=_event_key(self.run_id, "run_started"),
                run_id=self.run_id,
                event_type=PipelineEventType.RUN_STARTED,
                commit_sha=self.commit_sha,
                occurred_at=occurred_at,
                lease_expires_at=occurred_at + self.lease_duration,
                extractor_versions=dict(sorted(self.extractor_versions.items())),
            )

    def heartbeat(self) -> None:
        self._heartbeat_index += 1
        occurred_at = self.timestamp()
        with self.session_scope() as session:
            append_pipeline_event(
                session,
                event_key=_event_key(
                    self.run_id, "heartbeat", str(self._heartbeat_index)
                ),
                run_id=self.run_id,
                event_type=PipelineEventType.RUN_HEARTBEAT,
                commit_sha=self.commit_sha,
                occurred_at=occurred_at,
                lease_expires_at=occurred_at + self.lease_duration,
            )

    def stage_started(self, name: str, *, input_count: int | None = None) -> None:
        self.append(
            f"stage:{name}:started",
            PipelineEventType.STAGE_STARTED,
            stage=name,
            input_count=input_count,
        )

    def stage_succeeded(self, name: str, outcome: StageOutcome) -> None:
        self.append(
            f"stage:{name}:succeeded",
            PipelineEventType.STAGE_SUCCEEDED,
            stage=name,
            input_count=outcome.input_count,
            output_count=outcome.output_count,
            error_count=outcome.error_count,
        )

    def stage_failed(
        self,
        name: str,
        *,
        reason_code: PipelineReasonCode,
        outcome: StageOutcome | None = None,
    ) -> None:
        self.append(
            f"stage:{name}:failed",
            PipelineEventType.STAGE_FAILED,
            stage=name,
            input_count=outcome.input_count if outcome is not None else None,
            output_count=outcome.output_count if outcome is not None else None,
            error_count=(max(1, outcome.error_count) if outcome is not None else 1),
            reason_code=reason_code,
        )

    def source_started(self, stage: str, source: str) -> None:
        self.append(
            f"source:{stage}:{source}:started",
            PipelineEventType.SOURCE_STARTED,
            stage=stage,
            source=source,
        )

    def source_terminal(self, stage: str, source: SourceOutcome) -> None:
        failed = source.status == "failed"
        event_type = (
            PipelineEventType.SOURCE_FAILED
            if failed
            else PipelineEventType.SOURCE_SUCCEEDED
        )
        reason = source.reason_code
        if failed and reason is None:
            reason = PipelineReasonCode.UNEXPECTED_ERROR
        if not failed and source.yielded_count == 0:
            reason = PipelineReasonCode.SOURCE_ZERO_YIELD
        self.append(
            f"source:{stage}:{source.name}:terminal",
            event_type,
            stage=stage,
            source=source.name,
            attempt_count=source.attempt_count,
            output_count=source.yielded_count,
            inserted_count=source.inserted_count,
            error_count=source.error_count,
            selector_failure_count=source.selector_failure_count,
            parsing_failure_count=source.parsing_failure_count,
            latest_successful_article_at=source.latest_successful_article_at,
            reason_code=reason,
        )

    def run_succeeded(self) -> None:
        self.append("run:succeeded", PipelineEventType.RUN_SUCCEEDED)

    def run_failed(self, reason_code: PipelineReasonCode) -> None:
        self.append(
            "run:failed",
            PipelineEventType.RUN_FAILED,
            reason_code=reason_code,
        )

    def health(self) -> RunHealth:
        now = self.timestamp()
        with self.session_scope() as session:
            return load_run_health(session, self.run_id, now=now)


def _validated_outcome(outcome: StageOutcome) -> StageOutcome:
    if not isinstance(outcome, StageOutcome):
        raise RuntimeConfigurationError("stage operations must return StageOutcome")
    input_count = _nonnegative(outcome.input_count, field="stage input_count")
    output_count = _nonnegative(outcome.output_count, field="stage output_count")
    error_count = _nonnegative(outcome.error_count, field="stage error_count")
    names: set[str] = set()
    normalized_sources: list[SourceOutcome] = []
    for source in outcome.sources:
        if not isinstance(source, SourceOutcome):
            raise RuntimeConfigurationError("stage sources must be SourceOutcome values")
        name = _validate_source_alias(source.name)
        if name in names:
            raise RuntimeConfigurationError("a stage cannot report one source twice")
        names.add(name)
        if source.status not in {"success", "degraded", "failed"}:
            raise RuntimeConfigurationError("source status must be success, degraded, or failed")
        reason = source.reason_code
        if reason is not None:
            reason = _safe_reason(reason, fallback=PipelineReasonCode.UNEXPECTED_ERROR)
        latest = source.latest_successful_article_at
        if latest is not None:
            latest = _as_utc(latest, field="latest_successful_article_at")
        attempt_count = _nonnegative(source.attempt_count, field="source attempt_count")
        yielded_count = _nonnegative(source.yielded_count, field="source yielded_count")
        inserted_count = _nonnegative(source.inserted_count, field="source inserted_count")
        error_count = _nonnegative(source.error_count, field="source error_count")
        selector_failure_count = _nonnegative(
            source.selector_failure_count, field="source selector_failure_count"
        )
        parsing_failure_count = _nonnegative(
            source.parsing_failure_count, field="source parsing_failure_count"
        )

        # Normalize custom stage telemetry before it reaches the ledger.  A
        # plugin cannot leave a stage open by saying "success" with a failure
        # reason the source-success event would reject.
        status = source.status
        if status == "success":
            if yielded_count == 0:
                status = "degraded"
                reason = PipelineReasonCode.SOURCE_ZERO_YIELD
            elif reason is not None:
                if (
                    reason in _SOURCE_WARNING_REASONS
                    and reason != PipelineReasonCode.SOURCE_ZERO_YIELD
                ):
                    status = "degraded"
                else:
                    status = "failed"
                    reason = PipelineReasonCode.UNEXPECTED_ERROR
        elif status == "degraded":
            if yielded_count == 0:
                reason = PipelineReasonCode.SOURCE_ZERO_YIELD
            elif (
                reason not in _SOURCE_WARNING_REASONS
                or reason == PipelineReasonCode.SOURCE_ZERO_YIELD
            ):
                status = "failed"
                reason = PipelineReasonCode.UNEXPECTED_ERROR
        else:  # status == "failed" is checked above
            reason = reason or PipelineReasonCode.UNEXPECTED_ERROR
            error_count = max(1, error_count)
        normalized_sources.append(
            SourceOutcome(
                name=name,
                status=status,
                attempt_count=attempt_count,
                yielded_count=yielded_count,
                inserted_count=inserted_count,
                error_count=error_count,
                selector_failure_count=selector_failure_count,
                parsing_failure_count=parsing_failure_count,
                latest_successful_article_at=latest,
                reason_code=reason,
            )
        )
    if any(not isinstance(artifact, ReleaseArtifact) for artifact in outcome.artifacts):
        raise RuntimeConfigurationError("stage artifacts must be ReleaseArtifact values")
    return StageOutcome(
        input_count=input_count,
        output_count=output_count,
        error_count=error_count,
        sources=tuple(normalized_sources),
        artifacts=tuple(outcome.artifacts),
    )


def _normalized_source_outcome(source: SourceOutcome) -> SourceOutcome:
    """Use the stage validator for a callback's one source observation."""
    return _validated_outcome(StageOutcome(sources=(source,))).sources[0]


@dataclass(slots=True)
class _StageBoundaryRecorder:
    """Track the source boundaries observed while one stage executes."""

    recorder: _RunRecorder
    stage: str
    started_sources: list[str] = field(default_factory=list)
    terminal_sources: dict[str, SourceOutcome] = field(default_factory=dict)

    def runtime(self) -> StageRuntime:
        return StageRuntime(
            _heartbeat=self.recorder.heartbeat,
            _source_started=self.source_started,
            _source_completed=self.source_completed,
        )

    def source_started(self, source_name: str) -> None:
        source_name = _validate_source_alias(source_name)
        if source_name in self.started_sources:
            raise RuntimeConfigurationError("a source cannot start twice in one stage")
        self.recorder.source_started(self.stage, source_name)
        self.started_sources.append(source_name)

    def source_completed(self, source: SourceOutcome) -> None:
        source = _normalized_source_outcome(source)
        if source.name not in self.started_sources:
            self.source_started(source.name)
        if source.name in self.terminal_sources:
            raise RuntimeConfigurationError("a source cannot finish twice in one stage")
        self.recorder.source_terminal(self.stage, source)
        self.terminal_sources[source.name] = source

    def close_missing(self, names: Iterable[str]) -> None:
        for source_name in names:
            source_name = _validate_source_alias(source_name)
            if source_name in self.terminal_sources:
                continue
            self.source_completed(_missing_source_outcome(source_name))


def _missing_source_outcome(name: str) -> SourceOutcome:
    return SourceOutcome(
        name=name,
        status="failed",
        attempt_count=0,
        yielded_count=0,
        inserted_count=0,
        error_count=1,
        selector_failure_count=0,
        parsing_failure_count=0,
        reason_code=PipelineReasonCode.UNEXPECTED_ERROR,
    )


def _health_is_candidate_safe(health: RunHealth) -> bool:
    """A candidate may only represent a fully completed healthy data run."""
    return all(stage.status == "succeeded" for stage in health.stages) and all(
        source.status == "healthy" for source in health.sources
    )


def _failure(
    recorder: _RunRecorder,
    *,
    stage: str,
    reason_code: PipelineReasonCode,
    outcome: StageOutcome | None = None,
    cause: BaseException | None = None,
) -> NoReturn:
    recorder.stage_failed(stage, reason_code=reason_code, outcome=outcome)
    recorder.heartbeat()
    recorder.run_failed(reason_code)
    health = recorder.health()
    error = PipelineExecutionFailed(
        run_id=recorder.run_id,
        stage=stage,
        reason_code=reason_code,
        health=health,
    )
    if cause is not None:
        raise error from cause
    raise error


def run_orchestrated_pipeline(
    stages: Iterable[PipelineStage],
    *,
    session_scope: SessionScopeFactory = get_core_session,
    run_id: str | None = None,
    commit_sha: str | None = None,
    extractor_versions: Mapping[str, str] | None = None,
    lease_duration: timedelta = _DEFAULT_LEASE,
    clock: Clock | None = None,
    prepare_release: bool = False,
    release_id: str | None = None,
    blob_store: BlobStore | None = None,
) -> OrchestratedRun:
    """Run existing stages through the ledger without publishing a release.

    Each start, heartbeat, and terminal write uses a separate session scope so
    the audit trail survives a process crash between stages.  A candidate is
    uploaded only after every prior child projection is healthy.  This
    function never calls ``promote_release`` or any deployment provider.
    """
    if not callable(session_scope):
        raise RuntimeConfigurationError("session_scope must create session context managers")
    if lease_duration <= timedelta(0):
        raise RuntimeConfigurationError("lease_duration must be positive")
    if release_id is not None and not prepare_release:
        raise RuntimeConfigurationError("release_id requires prepare_release=True")

    resolved_stages = tuple(stages)
    if not resolved_stages:
        raise RuntimeConfigurationError("an orchestrated run needs at least one stage")
    names: set[str] = set()
    for stage in resolved_stages:
        if not isinstance(stage, PipelineStage) or not callable(stage.operation):
            raise RuntimeConfigurationError("stages must be PipelineStage values")
        _validate_stage_name(stage.name)
        if stage.name in names:
            raise RuntimeConfigurationError("stage names must be unique")
        names.add(stage.name)
        if len(set(stage.source_names)) != len(stage.source_names):
            raise RuntimeConfigurationError("configured source names must be unique")
        for source in stage.source_names:
            _validate_source_alias(source)

    resolved_run_id = run_id or make_run_id()
    if not isinstance(resolved_run_id, str) or not resolved_run_id.strip() or len(resolved_run_id) > 100:
        raise RuntimeConfigurationError("run_id must contain 1-100 characters")
    resolved_commit = (commit_sha or default_commit_sha()).strip()
    if not resolved_commit or len(resolved_commit) > 64:
        raise RuntimeConfigurationError("commit_sha must contain 1-64 characters")
    versions = dict(extractor_versions or {})
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(version, str)
        or not version.strip()
        for name, version in versions.items()
    ):
        raise RuntimeConfigurationError("extractor_versions must be non-empty string pairs")

    recorder = _RunRecorder(
        session_scope=session_scope,
        run_id=resolved_run_id,
        commit_sha=resolved_commit,
        lease_duration=lease_duration,
        clock=clock or (lambda: datetime.now(timezone.utc)),
        extractor_versions=dict(sorted(versions.items())),
    )
    recorder.start()
    artifacts: list[ReleaseArtifact] = []

    for stage in resolved_stages:
        recorder.heartbeat()
        recorder.stage_started(stage.name)
        boundaries = _StageBoundaryRecorder(recorder=recorder, stage=stage.name)

        try:
            outcome = _validated_outcome(stage.operation(boundaries.runtime()))
        except Exception as exc:
            logger.exception("Pipeline stage failed: %s", stage.name)
            boundaries.close_missing((*stage.source_names, *boundaries.started_sources))
            _failure(
                recorder,
                stage=stage.name,
                reason_code=PipelineReasonCode.UNEXPECTED_ERROR,
                cause=exc,
            )

        outcome_by_source = {source.name: source for source in outcome.sources}
        callback_mismatch = any(
            outcome_by_source.get(source_name) != source
            for source_name, source in boundaries.terminal_sources.items()
        )
        if callback_mismatch:
            _failure(
                recorder,
                stage=stage.name,
                reason_code=PipelineReasonCode.UNEXPECTED_ERROR,
                outcome=outcome,
            )

        for source in outcome.sources:
            if source.name not in boundaries.terminal_sources:
                # Non-ingestion/custom stages may only know a source after
                # they return.  The core ingestion adapter uses its callback
                # and therefore records the precise per-source boundary.
                boundaries.source_completed(source)
        boundaries.close_missing(stage.source_names)

        failed_sources = [
            source
            for source in boundaries.terminal_sources.values()
            if source.status == "failed"
        ]
        if failed_sources or any(
            source_name not in outcome_by_source for source_name in stage.source_names
        ):
            _failure(
                recorder,
                stage=stage.name,
                reason_code=PipelineReasonCode.UPSTREAM_STAGE_FAILED,
                outcome=outcome,
            )

        recorder.stage_succeeded(stage.name, outcome)
        recorder.heartbeat()
        artifacts.extend(outcome.artifacts)

    candidate: ReleaseCandidate | None = None
    if prepare_release:
        provisional_health = recorder.health()
        if not artifacts or not _health_is_candidate_safe(provisional_health):
            recorder.heartbeat()
            recorder.stage_started("prepare_release", input_count=len(artifacts))
            _failure(
                recorder,
                stage="prepare_release",
                reason_code=PipelineReasonCode.UPSTREAM_STAGE_FAILED,
                outcome=StageOutcome(
                    input_count=len(artifacts),
                    output_count=0,
                    error_count=1,
                ),
            )

        recorder.heartbeat()
        recorder.stage_started("prepare_release", input_count=len(artifacts))
        try:
            with session_scope() as session:
                candidate = create_release_candidate(
                    session,
                    blob_store or get_blob_store(),
                    run_id=resolved_run_id,
                    commit_sha=resolved_commit,
                    artifacts=artifacts,
                    release_id=release_id,
                    occurred_at=recorder.timestamp(),
                )
        except Exception as exc:
            logger.exception("Release candidate preparation failed")
            reason = (
                PipelineReasonCode.RELEASE_CONTRACT_FAILED
                if isinstance(exc, CandidateIntegrityError)
                else PipelineReasonCode.UNEXPECTED_ERROR
            )
            _failure(
                recorder,
                stage="prepare_release",
                reason_code=reason,
                outcome=StageOutcome(
                    input_count=len(artifacts), output_count=0, error_count=1
                ),
                cause=exc,
            )
        recorder.stage_succeeded(
            "prepare_release",
            StageOutcome(input_count=len(artifacts), output_count=1),
        )
        recorder.heartbeat()

    recorder.run_succeeded()
    return OrchestratedRun(
        run_id=resolved_run_id,
        commit_sha=resolved_commit,
        health=recorder.health(),
        release_candidate=candidate,
    )


def run_core_pipeline(
    *,
    translation_mode: str = "skip",
    bake_out_path: Path | None = None,
    prepare_release: bool = False,
    release_id: str | None = None,
    extractor_versions: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> OrchestratedRun:
    """Run the core data stages with ledger evidence, not a release claim."""
    if prepare_release and bake_out_path is None:
        raise RuntimeConfigurationError(
            "prepare_release requires bake_out_path so the candidate has a complete snapshot"
        )
    translation_mode = _validated_translation_mode(translation_mode)
    if extractor_versions is None:
        extractor_versions = core_component_versions(
            translation_mode=translation_mode
        )
    return run_orchestrated_pipeline(
        build_core_pipeline_stages(
            translation_mode=translation_mode,
            bake_out_path=bake_out_path,
        ),
        prepare_release=prepare_release,
        release_id=release_id,
        extractor_versions=extractor_versions,
        **kwargs,
    )
