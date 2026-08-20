"""Verify a pinned dashboard consumer against the producer contract bundle.

The check is deliberately local and deterministic. CI checks out the exact
portfolio commit named by the producer-owned consumer lock, then this script
compares the vendored bundle byte-for-byte and verifies the public route/data
source invariants before the consumer's own contract test and build run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CONSUMER_REPOSITORY = "https://github.com/gdayoub/george-portfolio.git"
EXPECTED_PRODUCER_REPOSITORY = (
    "https://github.com/gdayoub/arabic-osint-intelligence-platform.git"
)
EXPECTED_PUBLIC_ROUTE = "/osint-dashboard.html"
EXPECTED_PUBLIC_URL = (
    "https://george-dayoub-portfolio.vercel.app/osint-dashboard.html"
)
EXPECTED_DASHBOARD_DATA_URL = (
    "https://arabic-osint-dashboard.georgedayoub500.workers.dev/data.json"
)
DEFAULT_CHECKPOINT = (
    PROJECT_ROOT
    / "contracts"
    / "dashboard"
    / "consumers"
    / "george-portfolio.lock.json"
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_json(path: Path, label: str, problems: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"missing {label}: {path}")
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.append(f"invalid {label}: {exc}")
        return None
    if not isinstance(value, dict):
        problems.append(f"invalid {label}: expected a JSON object")
        return None
    return value


def _field(
    value: dict[str, Any], key: str, label: str, problems: list[str]
) -> Any | None:
    if key not in value:
        problems.append(f"missing {label}.{key}")
        return None
    return value[key]


def _expect_equal(
    actual: Any, expected: Any, label: str, problems: list[str]
) -> None:
    if actual != expected:
        problems.append(f"{label} mismatch: expected {expected!r}, found {actual!r}")


def _safe_artifact_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        return None
    return value


def _manifest_artifacts(
    manifest: dict[str, Any], bundle_root: Path, label: str, problems: list[str]
) -> dict[str, bytes]:
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        problems.append(f"invalid {label}.artifacts: expected a list")
        return {}

    artifacts: dict[str, bytes] = {}
    bundle_hasher = hashlib.sha256()
    for index, row in enumerate(rows):
        row_label = f"{label}.artifacts[{index}]"
        if not isinstance(row, dict):
            problems.append(f"invalid {row_label}: expected an object")
            continue
        relative_path = _safe_artifact_path(row.get("path"))
        if relative_path is None:
            problems.append(f"invalid {row_label}.path")
            continue
        if relative_path in artifacts:
            problems.append(f"duplicate {row_label}.path: {relative_path}")
            continue

        artifact_path = bundle_root / relative_path
        try:
            content = artifact_path.read_bytes()
        except OSError as exc:
            problems.append(f"cannot read {label} artifact {relative_path}: {exc}")
            continue
        artifacts[relative_path] = content

        _expect_equal(
            len(content),
            row.get("bytes"),
            f"{label} {relative_path} bytes",
            problems,
        )
        digest = _sha256(content)
        _expect_equal(
            digest,
            row.get("sha256"),
            f"{label} {relative_path} sha256",
            problems,
        )
        bundle_hasher.update(relative_path.encode("utf-8"))
        bundle_hasher.update(b"\0")
        bundle_hasher.update(digest.encode("ascii"))
        bundle_hasher.update(b"\n")

    _expect_equal(
        bundle_hasher.hexdigest(),
        manifest.get("bundle_sha256"),
        f"{label}.bundle_sha256",
        problems,
    )
    return artifacts


def _git_revision(repository_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def verify_dashboard_consumer(
    producer_root: Path,
    consumer_root: Path,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    *,
    consumer_revision: str | None = None,
) -> list[str]:
    """Return deterministic diagnostics; an empty list means compatible."""

    problems: list[str] = []
    checkpoint = _load_json(checkpoint_path, "consumer checkpoint", problems)
    if checkpoint is None:
        return problems

    producer_contract_root = producer_root / "contracts" / "dashboard"
    producer_lock = _load_json(
        producer_contract_root / "contract.lock.json",
        "producer contract lock",
        problems,
    )
    consumer_settings = checkpoint.get("consumer")
    if not isinstance(consumer_settings, dict):
        problems.append("invalid consumer checkpoint.consumer: expected a JSON object")
        return problems
    if producer_lock is None:
        return problems

    contract_version = _field(
        checkpoint, "contract_version", "consumer checkpoint", problems
    )
    manifest_relative = _field(checkpoint, "manifest", "consumer checkpoint", problems)
    expected_revision = _field(
        consumer_settings,
        "commit",
        "consumer checkpoint.consumer",
        problems,
    )
    consumer_lock_relative = _field(
        consumer_settings, "contract_lock", "consumer checkpoint.consumer", problems
    )

    _expect_equal(
        checkpoint.get("contract"),
        producer_lock.get("contract"),
        "checkpoint contract",
        problems,
    )
    _expect_equal(
        contract_version,
        producer_lock.get("current_version"),
        "checkpoint contract_version",
        problems,
    )
    for key in ("schema_version", "manifest_sha256", "bundle_sha256"):
        _expect_equal(
            checkpoint.get(key), producer_lock.get(key), f"checkpoint {key}", problems
        )
    _expect_equal(
        manifest_relative,
        producer_lock.get("manifest"),
        "checkpoint manifest",
        problems,
    )
    _expect_equal(
        consumer_settings.get("repository"),
        EXPECTED_CONSUMER_REPOSITORY,
        "consumer repository",
        problems,
    )

    if not isinstance(contract_version, str) or not isinstance(manifest_relative, str):
        return problems
    expected_manifest_relative = f"{contract_version}/manifest.json"
    _expect_equal(
        manifest_relative,
        expected_manifest_relative,
        "checkpoint manifest path",
        problems,
    )

    producer_manifest_path = producer_contract_root / manifest_relative
    producer_manifest = _load_json(
        producer_manifest_path, "producer manifest", problems
    )
    if producer_manifest is None:
        return problems
    producer_manifest_content = producer_manifest_path.read_bytes()
    _expect_equal(
        _sha256(producer_manifest_content),
        checkpoint.get("manifest_sha256"),
        "producer manifest sha256",
        problems,
    )
    for key in ("contract", "schema_version", "bundle_sha256"):
        _expect_equal(
            producer_manifest.get(key),
            checkpoint.get(key),
            f"producer manifest {key}",
            problems,
        )
    _expect_equal(
        producer_manifest.get("contract_version"),
        contract_version,
        "producer manifest contract_version",
        problems,
    )
    producer_artifacts = _manifest_artifacts(
        producer_manifest,
        producer_manifest_path.parent,
        "producer manifest",
        problems,
    )

    if not isinstance(expected_revision, str):
        problems.append("invalid consumer checkpoint.consumer.commit")
    elif len(expected_revision) != 40 or any(
        character not in "0123456789abcdef" for character in expected_revision
    ):
        problems.append(
            "invalid consumer checkpoint.consumer.commit: expected a full SHA"
        )
    else:
        actual_revision = consumer_revision or _git_revision(consumer_root)
        if actual_revision is None:
            problems.append(f"cannot resolve consumer git revision: {consumer_root}")
        else:
            _expect_equal(
                actual_revision,
                expected_revision,
                "consumer git revision",
                problems,
            )

    if not isinstance(consumer_lock_relative, str):
        problems.append("invalid consumer checkpoint.consumer.contract_lock")
        return problems
    consumer_lock = _load_json(
        consumer_root / consumer_lock_relative, "consumer contract lock", problems
    )
    if consumer_lock is None:
        return problems
    for checkpoint_key, consumer_key in (
        ("contract", "contract"),
        ("contract_version", "contract_version"),
        ("schema_version", "schema_version"),
        ("manifest", "manifest"),
        ("manifest_sha256", "manifest_sha256"),
        ("bundle_sha256", "bundle_sha256"),
    ):
        _expect_equal(
            consumer_lock.get(consumer_key),
            checkpoint.get(checkpoint_key),
            f"consumer lock {consumer_key}",
            problems,
        )
    producer_metadata = consumer_lock.get("producer")
    if not isinstance(producer_metadata, dict):
        problems.append("invalid consumer contract lock.producer")
    else:
        _expect_equal(
            producer_metadata.get("commit"),
            checkpoint.get("producer_contract_commit"),
            "consumer lock producer.commit",
            problems,
        )
        _expect_equal(
            producer_metadata.get("repository"),
            EXPECTED_PRODUCER_REPOSITORY,
            "consumer lock producer.repository",
            problems,
        )

    consumer_manifest_path = (
        consumer_root / "contracts" / "dashboard" / manifest_relative
    )
    consumer_manifest = _load_json(
        consumer_manifest_path, "consumer manifest", problems
    )
    if consumer_manifest is not None:
        consumer_manifest_content = consumer_manifest_path.read_bytes()
        if consumer_manifest_content != producer_manifest_content:
            problems.append("consumer manifest differs from producer manifest")
        consumer_artifacts = _manifest_artifacts(
            consumer_manifest,
            consumer_manifest_path.parent,
            "consumer manifest",
            problems,
        )
        for relative_path, producer_content in producer_artifacts.items():
            consumer_content = consumer_artifacts.get(relative_path)
            if consumer_content is not None and consumer_content != producer_content:
                problems.append(
                    f"consumer artifact differs from producer: {relative_path}"
                )

        expected_files = {"manifest.json", *producer_artifacts}
        actual_files = {
            path.relative_to(consumer_manifest_path.parent).as_posix()
            for path in consumer_manifest_path.parent.rglob("*")
            if path.is_file()
        }
        for relative_path in sorted(actual_files - expected_files):
            problems.append(f"unexpected consumer contract artifact: {relative_path}")
        for relative_path in sorted(expected_files - actual_files):
            problems.append(f"missing consumer contract artifact: {relative_path}")

    public_route = checkpoint.get("public_route")
    public_url = checkpoint.get("public_url")
    _expect_equal(public_route, EXPECTED_PUBLIC_ROUTE, "public route", problems)
    _expect_equal(public_url, EXPECTED_PUBLIC_URL, "public URL", problems)
    if not isinstance(public_route, str) or not public_route.startswith("/"):
        problems.append("invalid consumer checkpoint.public_route")
    if not isinstance(public_url, str):
        problems.append("invalid consumer checkpoint.public_url")
    elif isinstance(public_route, str):
        parsed_public_url = urlparse(public_url)
        _expect_equal(
            parsed_public_url.scheme, "https", "public URL scheme", problems
        )
        _expect_equal(
            parsed_public_url.netloc,
            "george-dayoub-portfolio.vercel.app",
            "public URL host",
            problems,
        )
        _expect_equal(
            parsed_public_url.path, public_route, "public URL path", problems
        )
        if parsed_public_url.query or parsed_public_url.fragment:
            problems.append("public URL must not contain a query or fragment")
        route_file = consumer_root / "public" / public_route.removeprefix("/")
        if not route_file.is_file():
            problems.append(f"missing portfolio public route file: {route_file}")

    data_url = checkpoint.get("dashboard_data_url")
    _expect_equal(
        data_url,
        EXPECTED_DASHBOARD_DATA_URL,
        "dashboard data URL",
        problems,
    )
    required_consumers = checkpoint.get("required_data_consumers")
    if not isinstance(data_url, str):
        problems.append("invalid consumer checkpoint.dashboard_data_url")
    if not isinstance(required_consumers, list) or not all(
        isinstance(path, str) for path in required_consumers
    ):
        problems.append("invalid consumer checkpoint.required_data_consumers")
    elif isinstance(data_url, str):
        for relative_path in required_consumers:
            path = consumer_root / relative_path
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(
                    f"cannot read required data consumer {relative_path}: {exc}"
                )
                continue
            if data_url not in content:
                problems.append(
                    "required data consumer does not use pinned dashboard URL: "
                    f"{relative_path}"
                )

    package = _load_json(
        consumer_root / "package.json", "consumer package", problems
    )
    contract_test = consumer_settings.get("contract_test")
    if package is not None:
        scripts = package.get("scripts")
        if not isinstance(scripts, dict) or "test:osint-contract" not in scripts:
            problems.append("consumer package is missing test:osint-contract")
    _expect_equal(
        contract_test,
        "npm run test:osint-contract",
        "consumer checkpoint contract_test",
        problems,
    )

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--consumer-root",
        type=Path,
        required=True,
        help="path to the checked-out portfolio repository",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT,
        help="producer-owned consumer checkpoint lock",
    )
    args = parser.parse_args()

    problems = verify_dashboard_consumer(
        PROJECT_ROOT,
        args.consumer_root.resolve(),
        args.checkpoint.resolve(),
    )
    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(1)

    checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    print(
        "dashboard consumer compatible: "
        f"{checkpoint['consumer']['commit']} -> "
        f"{checkpoint['public_url']}"
    )


if __name__ == "__main__":
    main()
