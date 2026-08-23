"""Static guardrails for the narrow, ledger-only GitHub Actions activation."""

from __future__ import annotations

from pathlib import Path

import yaml


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


def _triggers(parsed_workflow: dict) -> dict:
    """PyYAML resolves the unquoted ``on:`` key to the boolean ``True``."""
    return parsed_workflow.get(True, parsed_workflow.get("on", {}))


def test_pipeline_dispatch_form_cannot_select_backfill_before_its_deploy_step():
    """Task 2 fix: pipeline.yml ends in an unconditional public Cloudflare
    deploy, so its workflow_dispatch form must structurally be unable to
    request a backfill-sized run -- not just default away from one. A human
    who wants pipeline_mode=backfill must use backfill.yml, which has no
    deploy step to guard in the first place (docs/adr/0032)."""
    repository_root = Path(__file__).resolve().parents[2]
    pipeline_path = repository_root / ".github" / "workflows" / "pipeline.yml"
    parsed = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))

    pipeline_mode_input = _triggers(parsed)["workflow_dispatch"]["inputs"]["pipeline_mode"]
    assert pipeline_mode_input["type"] == "choice"
    assert pipeline_mode_input["options"] == ["scheduled"]
    assert "backfill" not in pipeline_mode_input["options"]

    text = pipeline_path.read_text(encoding="utf-8")
    # The run script must hardcode the mode rather than read it from an
    # input -- belt-and-suspenders alongside the choice-list restriction
    # above, so a future edit that adds "backfill" back to `options:` still
    # cannot reach the deploy step without this line also changing.
    assert "--pipeline-mode scheduled \\" in text
    assert "inputs.pipeline_mode" not in text


def test_pipeline_deploy_steps_are_unconditional_and_follow_the_ledgered_run():
    """The deploy steps below run every time this job reaches them -- which
    is exactly why pipeline_mode must be structurally locked to scheduled
    above rather than guarded with an `if:` here."""
    repository_root = Path(__file__).resolve().parents[2]
    pipeline_path = repository_root / ".github" / "workflows" / "pipeline.yml"
    parsed = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))

    steps = parsed["jobs"]["run"]["steps"]
    step_names = [step.get("run") or step.get("uses") for step in steps]
    ledger_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Run ledgered core pipeline"
    )
    deploy_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Deploy dashboard"
    )
    assert deploy_index > ledger_index
    for step in steps[ledger_index:]:
        assert "if" not in step, f"unexpected conditional guard on step: {step.get('name') or step.get('run')}"
    assert "cloudflare/wrangler-action@v3" in step_names[deploy_index]


def test_cron_trigger_has_no_inputs_context_to_read_a_backfill_override():
    repository_root = Path(__file__).resolve().parents[2]
    pipeline_path = repository_root / ".github" / "workflows" / "pipeline.yml"
    parsed = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))

    triggers = _triggers(parsed)
    assert "schedule" in triggers
    # The schedule trigger config carries only the cron expression -- no
    # inputs of any kind exist for GitHub to populate on that trigger, so
    # there is no `inputs.*` value a cron firing could read at all.
    assert triggers["schedule"] == [{"cron": "0 */6 * * *"}]


def test_backfill_workflow_is_manual_dispatch_only_with_a_required_reason():
    repository_root = Path(__file__).resolve().parents[2]
    backfill_path = repository_root / ".github" / "workflows" / "backfill.yml"
    parsed = yaml.safe_load(backfill_path.read_text(encoding="utf-8"))

    triggers = _triggers(parsed)
    assert set(triggers.keys()) == {"workflow_dispatch"}

    reason_input = triggers["workflow_dispatch"]["inputs"]["reason"]
    assert reason_input["required"] is True
    assert reason_input["type"] == "string"

    job = parsed["jobs"]["run"]
    assert job["timeout-minutes"] == 120
    assert parsed["concurrency"]["group"] == "osint-neon-writer"
    assert parsed["concurrency"]["cancel-in-progress"] is False


def test_backfill_workflow_records_backfill_mode_and_never_deploys():
    repository_root = Path(__file__).resolve().parents[2]
    backfill_path = repository_root / ".github" / "workflows" / "backfill.yml"
    text = backfill_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)

    assert "--pipeline-mode backfill \\" in text
    step_content = " ".join(
        str(value)
        for step in parsed["jobs"]["run"]["steps"]
        for value in step.values()
    )
    assert "cloudflare/wrangler-action" not in step_content
    assert "dashboard.html" not in step_content
    assert "country.html" not in step_content
    assert "dist/index.html" not in step_content
    # It still runs the same pre-write gates as the scheduled workflow.
    assert "python scripts/generate_dashboard_contract.py --check" in text
    assert "python -m pytest tests/unit -q" in text
    assert "python scripts/verify_core_schema.py" in text


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
