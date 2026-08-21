"""Static guardrails for the narrow, ledger-only GitHub Actions activation."""

from __future__ import annotations

from pathlib import Path


def test_pipeline_uses_one_ledgered_cli_and_keeps_the_direct_deploy_boundary():
    repository_root = Path(__file__).resolve().parents[2]
    pipeline = (
        repository_root / ".github" / "workflows" / "pipeline.yml"
    ).read_text(encoding="utf-8")

    assert "python main.py run-core-pipeline" in pipeline
    for legacy_step in (
        "python main.py ingest-core",
        "python main.py process-core",
        "python main.py extract-core",
        "python main.py resolve-core",
        "python main.py translate-core",
        "python main.py bake-dashboard",
    ):
        assert legacy_step not in pipeline
    assert "--run-id \"github-${{ github.run_id }}-${{ github.run_attempt }}\"" in pipeline
    assert "--commit-sha \"${{ github.sha }}\"" in pipeline
    assert "--translation-mode \"$translation_mode\"" in pipeline
    assert "cp src/api/static/dashboard.html dist/index.html" in pipeline
    assert "cp src/api/static/country.html dist/country.html" in pipeline
    assert "cloudflare/wrangler-action@v3" in pipeline
    assert "release_id" not in pipeline
    assert "promote_release" not in pipeline


def test_monitor_shares_writer_lock_and_only_reconciles_expired_run_leases():
    repository_root = Path(__file__).resolve().parents[2]
    monitor = (
        repository_root / ".github" / "workflows" / "pipeline-ledger-monitor.yml"
    ).read_text(encoding="utf-8")

    assert "group: osint-neon-writer" in monitor
    assert "cancel-in-progress: false" in monitor
    assert "python scripts/verify_core_schema.py" in monitor
    assert "python main.py reconcile-pipeline-runs" in monitor
    assert "reconcile_pending_promotion" not in monitor
    assert "prepare_release" not in monitor
    assert "cloudflare/wrangler-action" not in monitor
