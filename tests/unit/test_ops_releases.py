"""Release candidates stay immutable and promotion order cannot move backward."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.ops.events import PipelineEventType
from src.ops.ledger import InvalidEventTransition, append_pipeline_event
from src.ops.releases import (
    CandidateIntegrityError,
    DeploymentStatus,
    PromotionPlan,
    PromotionUncertain,
    ReleaseArtifact,
    ReleasePublishFailed,
    StaleReleaseCandidate,
    artifact_blob_key,
    begin_promotion,
    create_release_candidate,
    load_release_candidate,
    materialize_release_candidate,
    promote_release,
    publication_snapshot,
    reconcile_pending_promotion,
    rollback_release,
)
from src.store.orm import CoreBase, PipelineEventORM


BASE_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
COMMIT_SHA = "0123456789abcdef"


class MemoryBlobStore:
    def __init__(self, *, fail_on_put: int | None = None) -> None:
        self.values: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.put_count = 0
        self.fail_on_put = fail_on_put

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.put_count += 1
        if self.put_count == self.fail_on_put:
            raise OSError("simulated interrupted upload")
        self.values[key] = data
        self.content_types[key] = content_type

    def get(self, key: str) -> bytes:
        try:
            return self.values[key]
        except KeyError:
            raise KeyError(key) from None

    def exists(self, key: str) -> bool:
        return key in self.values


class FakePublisher:
    def __init__(self) -> None:
        self.live: tuple[str, str, int] | None = None
        self.publish_calls: list[PromotionPlan] = []
        self.published_content_types: dict[str, str] = {}
        self.raise_on_status = False

    def publish(
        self,
        plan: PromotionPlan,
        artifacts: tuple[ReleaseArtifact, ...],
    ) -> None:
        self.publish_calls.append(plan)
        self.published_content_types = {
            artifact.path: artifact.content_type for artifact in artifacts
        }
        self.live = (
            plan.candidate.release_id,
            plan.candidate.manifest_sha256,
            plan.promotion_sequence,
        )

    def status(self, plan: PromotionPlan) -> DeploymentStatus:
        if self.raise_on_status:
            raise OSError("simulated provider status failure")
        expected = (
            plan.candidate.release_id,
            plan.candidate.manifest_sha256,
            plan.promotion_sequence,
        )
        if self.live == expected:
            return DeploymentStatus.LIVE
        return DeploymentStatus.NOT_LIVE


class NotLivePublisher(FakePublisher):
    def publish(
        self,
        plan: PromotionPlan,
        artifacts: tuple[ReleaseArtifact, ...],
    ) -> None:
        self.publish_calls.append(plan)


@pytest.fixture()
def release_database():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CoreBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _start_run(factory, run_id: str, offset: int) -> None:  # noqa: ANN001
    started_at = BASE_TIME + timedelta(seconds=offset)
    with factory() as session:
        append_pipeline_event(
            session,
            event_key=f"{run_id}:started",
            run_id=run_id,
            event_type=PipelineEventType.RUN_STARTED,
            commit_sha=COMMIT_SHA,
            occurred_at=started_at,
            lease_expires_at=started_at + timedelta(minutes=5),
        )
        session.commit()


def _candidate(
    factory,  # noqa: ANN001
    store: MemoryBlobStore,
    name: str,
    offset: int,
):
    run_id = f"run-{name}"
    _start_run(factory, run_id, offset)
    with factory() as session:
        candidate = create_release_candidate(
            session,
            store,
            run_id=run_id,
            commit_sha=COMMIT_SHA,
            release_id=f"release-{name}",
            occurred_at=BASE_TIME + timedelta(seconds=offset + 1),
            artifacts=[
                ReleaseArtifact(
                    path="data.json",
                    data=(f'{{"release":"{name}","title":"مرحبا"}}\n').encode(),
                    content_type="application/json; charset=utf-8",
                ),
                ReleaseArtifact(
                    path="assets/app.css",
                    data=f"/* {name} */\n".encode(),
                    content_type="text/css; charset=utf-8",
                ),
            ],
        )
        session.commit()
    return candidate


def test_newer_candidate_publishes_and_older_queued_candidate_is_rejected(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    publisher = FakePublisher()
    older = _candidate(release_database, store, "older", 0)
    newer = _candidate(release_database, store, "newer", 10)

    published = promote_release(release_database, store, publisher, newer)
    assert published.current_release_id == newer.release_id
    assert published.max_data_sequence_seen == newer.data_sequence
    assert newer.data_sequence > older.data_sequence

    with pytest.raises(StaleReleaseCandidate, match="not above high-water"):
        promote_release(release_database, store, publisher, older)

    assert len(publisher.publish_calls) == 1
    with release_database() as session:
        snapshot = publication_snapshot(session)
        assert snapshot.current_release_id == newer.release_id
        superseded = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.release_id == older.release_id,
                PipelineEventORM.event_type
                == PipelineEventType.RELEASE_SUPERSEDED.value,
            )
        )
        assert superseded is not None
        assert superseded.promotion_sequence == snapshot.promotion_sequence


def test_retry_of_exact_current_release_is_idempotent(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    publisher = FakePublisher()
    candidate = _candidate(release_database, store, "same-retry", 0)

    first = promote_release(release_database, store, publisher, candidate)
    retry = promote_release(release_database, store, publisher, candidate)

    assert retry == first
    assert len(publisher.publish_calls) == 1
    with release_database() as session:
        superseded_count = len(
            list(
                session.scalars(
                    select(PipelineEventORM).where(
                        PipelineEventORM.release_id == candidate.release_id,
                        PipelineEventORM.event_type
                        == PipelineEventType.RELEASE_SUPERSEDED.value,
                    )
                )
            )
        )
        assert superseded_count == 0


def test_rollback_repromotes_old_payload_without_lowering_data_high_water(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    publisher = FakePublisher()
    old = _candidate(release_database, store, "old", 0)
    queued = _candidate(release_database, store, "queued", 10)
    newest = _candidate(release_database, store, "newest", 20)

    first = promote_release(release_database, store, publisher, old)
    second = promote_release(release_database, store, publisher, newest)
    assert second.promotion_sequence > first.promotion_sequence

    _start_run(release_database, "run-rollback", 30)
    rolled_back = rollback_release(
        release_database,
        store,
        publisher,
        target_release_id=old.release_id,
        run_id="run-rollback",
        commit_sha="fedcba9876543210",
        operation_id="rollback-to-old",
    )

    assert rolled_back.current_release_id == old.release_id
    assert rolled_back.current_data_sequence == old.data_sequence
    assert rolled_back.max_data_sequence_seen == newest.data_sequence
    assert rolled_back.promotion_sequence > second.promotion_sequence

    with pytest.raises(StaleReleaseCandidate):
        promote_release(release_database, store, publisher, queued)

    with release_database() as session:
        rollback_event = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.event_type
                == PipelineEventType.RELEASE_PUBLISHED.value,
                PipelineEventORM.promotion_sequence
                == rolled_back.promotion_sequence,
            )
        )
        assert rollback_event is not None
        assert rollback_event.rollback_of_promotion_sequence == second.promotion_sequence


def test_interrupted_upload_registers_no_candidate_or_manifest(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore(fail_on_put=2)
    _start_run(release_database, "run-interrupted", 0)

    with release_database() as session:
        with pytest.raises(OSError, match="interrupted upload"):
            create_release_candidate(
                session,
                store,
                run_id="run-interrupted",
                commit_sha=COMMIT_SHA,
                release_id="release-interrupted",
                occurred_at=BASE_TIME + timedelta(seconds=1),
                artifacts=[
                    ReleaseArtifact("a.json", b"{}\n", "application/json"),
                    ReleaseArtifact("b.json", b"[]\n", "application/json"),
                ],
            )
        session.commit()

    with release_database() as session:
        release_events = list(
            session.scalars(
                select(PipelineEventORM).where(
                    PipelineEventORM.release_id == "release-interrupted"
                )
            )
        )
        assert release_events == []
    assert not any("/manifests/" in key for key in store.values)


def test_ledger_rejects_candidate_without_matching_reservation(
    release_database,  # noqa: ANN001
):
    _start_run(release_database, "run-invalid-release", 0)
    with release_database() as session:
        with pytest.raises(InvalidEventTransition, match="has no reservation"):
            append_pipeline_event(
                session,
                event_key="invalid-release:candidate",
                run_id="run-invalid-release",
                release_id="invalid-release",
                event_type=PipelineEventType.RELEASE_CANDIDATE_CREATED,
                commit_sha=COMMIT_SHA,
                data_sequence=99,
                manifest_sha256="a" * 64,
            )


def test_initial_ledger_migration_reserves_complete_release_vocabulary():
    revision = import_module("migrations.versions.0002_pipeline_event_ledger")
    planned_release_events = {
        "release_reserved",
        "release_candidate_created",
        "promotion_started",
        "release_published",
        "release_failed",
        "release_superseded",
    }

    assert planned_release_events.issubset(set(revision._EVENT_TYPES))
    assert planned_release_events.issubset(
        {event_type.value for event_type in PipelineEventType}
    )


def test_manifest_and_artifact_hashes_are_verified_with_real_content_types(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    candidate = _candidate(release_database, store, "verified", 0)

    assert store.content_types[candidate.manifest_key] == (
        "application/json; charset=utf-8"
    )
    assert {
        store.content_types[descriptor.blob_key] for descriptor in candidate.artifacts
    } == {
        "application/json; charset=utf-8",
        "text/css; charset=utf-8",
    }
    manifest_bytes = store.values[candidate.manifest_key]
    assert "مرحبا" not in manifest_bytes.decode("utf-8")

    loaded = load_release_candidate(
        store,
        manifest_key=candidate.manifest_key,
        expected_manifest_sha256=candidate.manifest_sha256,
    )
    first_artifact = loaded.artifacts[0]
    store.values[first_artifact.blob_key] = b"tampered"
    with pytest.raises(CandidateIntegrityError, match="does not match manifest"):
        materialize_release_candidate(store, loaded)

    store.values[candidate.manifest_key] = b'{"tampered":true}\n'
    with pytest.raises(CandidateIntegrityError, match="SHA-256"):
        load_release_candidate(
            store,
            manifest_key=candidate.manifest_key,
            expected_manifest_sha256=candidate.manifest_sha256,
        )


def test_same_bytes_with_different_media_types_use_different_blob_keys():
    digest = "a" * 64

    assert artifact_blob_key(digest, "application/json") != artifact_blob_key(
        digest,
        "text/plain; charset=utf-8",
    )


def test_killed_publisher_is_reconciled_from_durable_pending_plan(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    publisher = FakePublisher()
    candidate = _candidate(release_database, store, "reconcile", 0)

    with release_database() as session:
        plan = begin_promotion(
            session,
            candidate,
            operation_id="killed-after-deploy",
        )
        session.commit()

    publisher.publish(plan, materialize_release_candidate(store, candidate))
    with release_database() as session:
        pending = publication_snapshot(session)
        assert pending.current_release_id is None
        assert pending.pending_release_id == candidate.release_id
        assert pending.pending_promotion_sequence == plan.promotion_sequence

    reconciled = reconcile_pending_promotion(release_database, store, publisher)
    assert reconciled is not None
    assert reconciled.current_release_id == candidate.release_id
    assert reconciled.pending_release_id is None
    assert reconciled.promotion_sequence == plan.promotion_sequence

    with release_database() as session:
        published = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.promotion_sequence == plan.promotion_sequence,
                PipelineEventORM.event_type
                == PipelineEventType.RELEASE_PUBLISHED.value,
            )
        )
        assert published is not None


def test_status_connection_failure_keeps_plan_pending_for_reconciliation(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    publisher = FakePublisher()
    publisher.raise_on_status = True
    candidate = _candidate(release_database, store, "status-failure", 0)

    with pytest.raises(PromotionUncertain, match="needs reconciliation"):
        promote_release(release_database, store, publisher, candidate)

    with release_database() as session:
        pending = publication_snapshot(session)
        assert pending.current_release_id is None
        assert pending.pending_release_id == candidate.release_id

    publisher.raise_on_status = False
    reconciled = reconcile_pending_promotion(release_database, store, publisher)
    assert reconciled is not None
    assert reconciled.current_release_id == candidate.release_id
    assert reconciled.pending_release_id is None


def test_definitely_not_live_promotion_fails_without_advancing_state(
    release_database,  # noqa: ANN001
):
    store = MemoryBlobStore()
    publisher = NotLivePublisher()
    candidate = _candidate(release_database, store, "not-live", 0)

    with pytest.raises(ReleasePublishFailed, match="verified as not live"):
        promote_release(release_database, store, publisher, candidate)

    with release_database() as session:
        snapshot = publication_snapshot(session)
        assert snapshot.current_release_id is None
        assert snapshot.max_data_sequence_seen == 0
        assert snapshot.pending_release_id is None
        failed = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.release_id == candidate.release_id,
                PipelineEventORM.event_type
                == PipelineEventType.RELEASE_FAILED.value,
            )
        )
        assert failed is not None
        assert failed.promotion_sequence is not None
