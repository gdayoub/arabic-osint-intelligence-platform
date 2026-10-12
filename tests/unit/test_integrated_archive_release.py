"""Exercise the opt-in archive through the existing immutable candidate path."""

from contextlib import nullcontext
from datetime import datetime, timezone
import json

from sqlalchemy import select

import scripts.bake_dashboard_data as bake_module
from src.ops.events import PipelineEventType
from src.ops.releases import load_release_candidate, materialize_release_candidate
from src.ops.runtime import bake_stage, run_orchestrated_pipeline
from src.store.orm import PipelineEventORM
from tests.unit.test_atlas import bundle, seed


def test_archive_roundtrips_in_immutable_candidate_without_publication(
    session, ontology, blob_store, tmp_path, monkeypatch
):
    seed(session, ontology, blob_store, "integrated")
    data, pages, archive = bundle(session)

    def staged_archive(path, recent_limit=30):
        del recent_limit
        bake_module.publish_snapshot_bundle(data, pages, path, atlas=archive)
        return data

    # Explicit opt-in at the test boundary; the normal runtime default stays
    # v1-only. Both the staged bytes and candidate blobs are local fixtures.
    monkeypatch.setattr(bake_module, "run_bake", staged_archive)
    result = run_orchestrated_pipeline(
        (bake_stage(tmp_path / "data.json"),),
        session_scope=lambda: nullcontext(session),
        run_id="archive-candidate-regression",
        commit_sha="3a9bd6978fb57573ad323b8f6dcb81ccc8492922",
        clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc),
        prepare_release=True,
        release_id="archive-candidate-regression",
        blob_store=blob_store,
    )
    assert result.health.status == "healthy"
    candidate = result.release_candidate
    assert candidate is not None
    verified = load_release_candidate(
        blob_store,
        manifest_key=candidate.manifest_key,
        expected_manifest_sha256=candidate.manifest_sha256,
    )
    artifacts = {
        artifact.path: artifact.data
        for artifact in materialize_release_candidate(blob_store, verified)
    }
    assert json.loads(artifacts["countries/_atlas.json"]) == archive
    assert json.loads(artifacts["data.json"]) == data
    assert artifacts["countries/_atlas.json"] == (tmp_path / "countries/_atlas.json").read_bytes()
    assert {"index.html", "country.html", "high_risk.html"}.issubset(artifacts)
    event_types = set(session.scalars(select(PipelineEventORM.event_type)))
    assert PipelineEventType.RELEASE_CANDIDATE_CREATED.value in event_types
    assert PipelineEventType.PROMOTION_STARTED.value not in event_types
    assert PipelineEventType.RELEASE_PUBLISHED.value not in event_types
