"""Contract and publication checks for the public dashboard snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import scripts.bake_dashboard_data as bake_module
from scripts.bake_dashboard_data import publish_snapshot_bundle
from scripts.generate_dashboard_contract import (
    BUNDLE_DIR,
    build_contract_artifacts,
    check_contract_bundle,
)
from src.contracts.dashboard import (
    validate_dashboard_snapshot,
    validate_snapshot_bundle,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = PROJECT_ROOT / "contracts" / "dashboard"


def _fixture(name: str) -> dict:
    path = BUNDLE_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _bundle() -> tuple[dict, dict[str, dict]]:
    dashboard = _fixture("dashboard.fixture.json")
    country = _fixture("country.fixture.json")
    return dashboard, {country["country"]: country}


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "/relative/source",
        "https:///missing-host",
        "https://user:secret@example.com/source",
        " https://example.com/source",
        "https://example.com/a path",
        "https://example.com/\\@attacker.example",
        'https://example.com/\" onmouseover=\"alert(1)',
        "https://example.com/' onmouseover='alert(1)",
        "https://example.com/<script>",
        "https://example.com/>payload",
        "https://example.com/`payload",
    ],
)
def test_contract_rejects_unsafe_or_attribute_breaking_urls(unsafe_url):
    dashboard = _fixture("dashboard.fixture.json")
    dashboard["recent"][0]["url"] = unsafe_url

    with pytest.raises(ValidationError):
        validate_dashboard_snapshot(dashboard)


def test_contract_preserves_safe_urls_and_hostile_text_exactly():
    dashboard = _fixture("dashboard.fixture.json")
    original_title = dashboard["recent"][0]["title"]
    dashboard["recent"][0]["url"] = "HTTPS://EXAMPLE.COM/source?id=1&lang=ar"

    validated = validate_dashboard_snapshot(dashboard)

    assert validated["recent"][0]["title"] == original_title
    assert "<script>" in validated["recent"][0]["title"]
    assert validated["recent"][0]["url"] == "HTTPS://EXAMPLE.COM/source?id=1&lang=ar"


def test_contract_is_strict_and_rejects_unknown_fields():
    dashboard = _fixture("dashboard.fixture.json")
    dashboard["stats"]["total_raw"] = "2"
    dashboard["recent"][0]["not_in_contract"] = True

    with pytest.raises(ValidationError):
        validate_dashboard_snapshot(dashboard)


def test_bundle_requires_matching_timestamp_version_index_and_pages():
    dashboard, country_pages = _bundle()
    validate_snapshot_bundle(dashboard, country_pages)

    country_pages["Syria"]["generated_at"] = "2026-08-21T00:00:00+00:00"
    with pytest.raises(ValueError, match="timestamp does not match"):
        validate_snapshot_bundle(dashboard, country_pages)

    dashboard, country_pages = _bundle()
    country_pages["Syria"]["schema_version"] = 2
    with pytest.raises(ValidationError):
        validate_snapshot_bundle(dashboard, country_pages)

    dashboard, country_pages = _bundle()
    dashboard["countries"][0]["slug"] = "syria-updated"
    with pytest.raises(ValueError, match="index does not match"):
        validate_snapshot_bundle(dashboard, country_pages)


def test_contract_rejects_naive_timestamps_and_broken_aggregates():
    dashboard = _fixture("dashboard.fixture.json")
    dashboard["generated_at"] = "2026-08-20T00:00:00"
    with pytest.raises(ValidationError):
        validate_dashboard_snapshot(dashboard)

    dashboard = _fixture("dashboard.fixture.json")
    dashboard["stats"]["total_processed"] = 2
    with pytest.raises(ValidationError):
        validate_dashboard_snapshot(dashboard)


def test_generated_contract_bundle_is_deterministic_and_hashes_match():
    first = build_contract_artifacts()
    second = build_contract_artifacts()

    assert first == second
    assert check_contract_bundle() == []

    manifest = json.loads(first["manifest.json"])
    for artifact in manifest["artifacts"]:
        content = first[artifact["path"]]
        assert artifact["bytes"] == len(content)
        assert artifact["sha256"] == hashlib.sha256(content).hexdigest()

    lock = json.loads(
        (CONTRACT_ROOT / "contract.lock.json").read_text(encoding="utf-8")
    )
    assert lock["current_version"] == manifest["contract_version"]
    assert lock["bundle_sha256"] == manifest["bundle_sha256"]
    assert lock["manifest_sha256"] == hashlib.sha256(
        first["manifest.json"]
    ).hexdigest()


def test_publish_validates_everything_before_replacing_live_files(tmp_path):
    out_path = tmp_path / "public" / "data.json"
    countries_path = out_path.parent / "countries"
    countries_path.mkdir(parents=True)
    out_path.write_text("old dashboard", encoding="utf-8")
    old_country = countries_path / "syria.json"
    old_country.write_text("old country", encoding="utf-8")

    dashboard, country_pages = _bundle()
    country_pages["Syria"]["articles"][0]["url"] = "javascript:alert(1)"

    with pytest.raises(ValidationError):
        publish_snapshot_bundle(dashboard, country_pages, out_path)

    assert out_path.read_text(encoding="utf-8") == "old dashboard"
    assert old_country.read_text(encoding="utf-8") == "old country"


def test_publish_promotes_one_complete_bundle_and_preserves_hostile_text(tmp_path):
    out_path = tmp_path / "public" / "data.json"
    countries_path = out_path.parent / "countries"
    countries_path.mkdir(parents=True)
    out_path.write_text("old dashboard", encoding="utf-8")
    (countries_path / "stale.json").write_text("stale", encoding="utf-8")

    dashboard, country_pages = _bundle()
    published, published_countries = publish_snapshot_bundle(
        dashboard, country_pages, out_path
    )

    assert json.loads(out_path.read_text(encoding="utf-8")) == published
    assert {path.name for path in countries_path.iterdir()} == {"syria.json"}
    assert json.loads(
        (countries_path / "syria.json").read_text(encoding="utf-8")
    ) == published_countries["Syria"]
    assert "<script>" in out_path.read_text(encoding="utf-8")


def test_publish_restores_previous_bundle_when_promotion_fails(
    tmp_path, monkeypatch
):
    out_path = tmp_path / "public" / "data.json"
    countries_path = out_path.parent / "countries"
    countries_path.mkdir(parents=True)
    out_path.write_text("old dashboard", encoding="utf-8")
    (countries_path / "syria.json").write_text("old country", encoding="utf-8")

    real_replace = os.replace

    def fail_main_commit(source, destination):
        source_path = Path(source)
        if (
            source_path.name == "data.json"
            and source_path.parent.name.startswith(".dashboard-snapshot-stage-")
        ):
            raise OSError("simulated main snapshot promotion failure")
        return real_replace(source, destination)

    monkeypatch.setattr(bake_module.os, "replace", fail_main_commit)
    dashboard, country_pages = _bundle()

    with pytest.raises(OSError, match="simulated"):
        publish_snapshot_bundle(dashboard, country_pages, out_path)

    assert out_path.read_text(encoding="utf-8") == "old dashboard"
    assert (countries_path / "syria.json").read_text(encoding="utf-8") == "old country"
