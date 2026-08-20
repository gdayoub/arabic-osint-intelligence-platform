"""Tests for the pinned portfolio dashboard compatibility checkpoint."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.check_dashboard_consumer import (
    DEFAULT_CHECKPOINT,
    EXPECTED_DASHBOARD_DATA_URL,
    EXPECTED_PUBLIC_URL,
    verify_dashboard_consumer,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _checkpoint() -> dict:
    return json.loads(DEFAULT_CHECKPOINT.read_text(encoding="utf-8"))


def _make_consumer(tmp_path: Path) -> tuple[Path, str]:
    checkpoint = _checkpoint()
    consumer_root = tmp_path / "consumer"
    version = checkpoint["contract_version"]
    producer_bundle = PROJECT_ROOT / "contracts" / "dashboard" / version
    consumer_bundle = consumer_root / "contracts" / "dashboard" / version
    shutil.copytree(producer_bundle, consumer_bundle)

    portfolio_lock = {
        "bundle_sha256": checkpoint["bundle_sha256"],
        "contract": checkpoint["contract"],
        "contract_version": version,
        "manifest": checkpoint["manifest"],
        "manifest_sha256": checkpoint["manifest_sha256"],
        "producer": {
            "base_commit": "659f5289394b6173d1bbe75f379dc16dcf6cc9a4",
            "commit": checkpoint["producer_contract_commit"],
            "repository": (
                "https://github.com/gdayoub/"
                "arabic-osint-intelligence-platform.git"
            ),
        },
        "schema_version": checkpoint["schema_version"],
    }
    lock_path = consumer_root / checkpoint["consumer"]["contract_lock"]
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(portfolio_lock, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for relative_path in checkpoint["required_data_consumers"]:
        path = consumer_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(checkpoint["dashboard_data_url"] + "\n", encoding="utf-8")

    package_path = consumer_root / "package.json"
    package_path.write_text(
        json.dumps(
            {
                "scripts": {
                    "test:osint-contract": (
                        "node --test tests/osint-contract.test.mjs"
                    )
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return consumer_root, checkpoint["consumer"]["commit"]


def test_pinned_portfolio_bundle_route_and_data_source_are_compatible(tmp_path):
    consumer_root, revision = _make_consumer(tmp_path)
    checkpoint = _checkpoint()

    assert verify_dashboard_consumer(
        PROJECT_ROOT, consumer_root, consumer_revision=revision
    ) == []
    assert checkpoint["public_url"] == EXPECTED_PUBLIC_URL
    assert checkpoint["dashboard_data_url"] == EXPECTED_DASHBOARD_DATA_URL


def test_checkpoint_rejects_a_different_consumer_revision(tmp_path):
    consumer_root, _ = _make_consumer(tmp_path)

    problems = verify_dashboard_consumer(
        PROJECT_ROOT, consumer_root, consumer_revision="0" * 40
    )

    assert any("consumer git revision mismatch" in problem for problem in problems)


def test_checkpoint_rejects_vendored_artifact_drift(tmp_path):
    consumer_root, revision = _make_consumer(tmp_path)
    schema = (
        consumer_root
        / "contracts"
        / "dashboard"
        / "1.0.0"
        / "dashboard.schema.json"
    )
    schema.write_text("{}\n", encoding="utf-8")

    problems = verify_dashboard_consumer(
        PROJECT_ROOT, consumer_root, consumer_revision=revision
    )

    assert any(
        "dashboard.schema.json sha256 mismatch" in problem
        for problem in problems
    )
    assert any(
        "consumer artifact differs from producer: dashboard.schema.json" in problem
        for problem in problems
    )


def test_checkpoint_rejects_missing_public_route(tmp_path):
    consumer_root, revision = _make_consumer(tmp_path)
    (consumer_root / "public" / "osint-dashboard.html").unlink()

    problems = verify_dashboard_consumer(
        PROJECT_ROOT, consumer_root, consumer_revision=revision
    )

    assert any("missing portfolio public route file" in problem for problem in problems)


def test_checkpoint_rejects_a_changed_dashboard_data_source(tmp_path):
    consumer_root, revision = _make_consumer(tmp_path)
    (consumer_root / "app" / "api" / "osint" / "route.ts").write_text(
        "https://example.invalid/data.json\n", encoding="utf-8"
    )

    problems = verify_dashboard_consumer(
        PROJECT_ROOT, consumer_root, consumer_revision=revision
    )

    assert any(
        "required data consumer does not use pinned dashboard URL" in problem
        for problem in problems
    )
