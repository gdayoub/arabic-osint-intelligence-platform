"""Generate or verify the immutable public dashboard contract bundle.

Usage:
    python scripts/generate_dashboard_contract.py
    python scripts/generate_dashboard_contract.py --check

Generation is deterministic under the Pydantic pin recorded in ADR 0018.
Check mode is read-only and exits non-zero if a committed artifact drifted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import pydantic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.dashboard import (
    CONTRACT_VERSION,
    PYDANTIC_PIN,
    SCHEMA_VERSION,
    CountrySnapshot,
    DashboardSnapshot,
    validate_country_snapshot,
    validate_dashboard_snapshot,
)

BUNDLE_DIR = PROJECT_ROOT / "contracts" / "dashboard" / CONTRACT_VERSION
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _json_bytes(value: Any) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (rendered + "\n").encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _schema(model: type[DashboardSnapshot] | type[CountrySnapshot], name: str) -> dict[str, Any]:
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["$id"] = (
        f"urn:george-dayoub:arabic-osint:dashboard:{CONTRACT_VERSION}:{name}"
    )
    return schema


def _dashboard_fixture() -> dict[str, Any]:
    hostile_title = "خبر <script>alert('x')</script> & دليل عربي"
    return {
        "generated_at": "2026-08-20T00:00:00+00:00",
        "schema_version": SCHEMA_VERSION,
        "stats": {
            "total_raw": 2,
            "total_processed": 1,
            "sources": {
                "AlJazeeraArabic": 1,
                "BBCArabic": 1,
            },
        },
        "topics": {
            "topics": [
                {"topic": "Politics", "count": 1},
            ]
        },
        "escalation": {"escalation": {"high": 1}},
        "recent": [
            {
                "title": hostile_title,
                "title_en": "Arabic evidence <not markup>",
                "source": "AlJazeeraArabic",
                "url": "https://example.com/source?id=1&lang=ar",
                "topic": "Politics",
                "escalation": "high",
                "country": "Syria",
                "ai_summary": None,
                "processed_at": "2026-08-20T00:00:00+00:00",
                "published_date": "2026-08-19T23:00:00+00:00",
            },
            {
                "title": "وثيقة لم تكتمل معالجتها",
                "title_en": None,
                "source": "BBCArabic",
                "url": None,
                "topic": None,
                "escalation": None,
                "country": None,
                "ai_summary": None,
                "processed_at": "2026-08-19T22:00:00+00:00",
                "published_date": None,
            },
        ],
        "daily": [{"date": "2026-08-20", "count": 2}],
        "mentions": {
            "total": 2,
            "top": {"person": [{"name": "محمد أحمد", "count": 2}]},
        },
        "entities": {
            "total": 1,
            "top": {
                "person": [
                    {
                        "name": "محمد أحمد",
                        "count": 2,
                        "surface_forms": ["محمد أحمد", "محمد احمد"],
                    }
                ]
            },
        },
        "review_queue": {
            "items": [
                {
                    "id": 7,
                    "object_type": "person",
                    "score": 0.58,
                    "threshold": 0.60,
                    "distance": 0.02,
                    "features": {
                        "name_similarity": 0.92,
                        "key_overlap": 0.50,
                        "co_mention_overlap": 0.25,
                        "temporal_proximity": 0.75,
                        "same_source": 0.0,
                        "same_type": 1.0,
                    },
                    "left": {
                        "mention_id": 10,
                        "text": "محمد أحمد",
                        "source": "AlJazeeraArabic",
                        "url": "https://example.com/source?id=1&lang=ar",
                        "title": hostile_title,
                    },
                    "right": {
                        "mention_id": 11,
                        "text": "محمد احمد",
                        "source": "BBCArabic",
                        "url": "https://example.org/second",
                        "title": "عنوان الدليل الثاني",
                    },
                }
            ]
        },
        "countries": [{"country": "Syria", "slug": "syria", "count": 1}],
    }


def _country_fixture() -> dict[str, Any]:
    return {
        "generated_at": "2026-08-20T00:00:00+00:00",
        "schema_version": SCHEMA_VERSION,
        "country": "Syria",
        "slug": "syria",
        "total": 1,
        "sources": {"AlJazeeraArabic": 1},
        "topics": [{"topic": "Politics", "count": 1}],
        "escalation": {"high": 1},
        "daily": [{"date": "2026-08-20", "count": 1}],
        "articles": [
            {
                "title": "خبر <script>alert('x')</script> & دليل عربي",
                "title_en": "Arabic evidence <not markup>",
                "source": "AlJazeeraArabic",
                "url": "https://example.com/source?id=1&lang=ar",
                "topic": "Politics",
                "escalation": "high",
                "published_date": "2026-08-19T23:00:00+00:00",
                "processed_at": "2026-08-20T00:00:00+00:00",
            }
        ],
    }


def _typescript_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _typescript_type(schema: Mapping[str, Any]) -> str:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return reference.rsplit("/", 1)[-1]

    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(_typescript_type(option) for option in schema["anyOf"])

    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        items = schema.get("items")
        return f"Array<{_typescript_type(items) if isinstance(items, Mapping) else 'unknown'}>"
    if schema_type == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, Mapping):
            return f"Record<string, {_typescript_type(additional)}>"
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            required = set(schema.get("required", []))
            pieces = []
            for key, value in properties.items():
                marker = "" if key in required else "?"
                pieces.append(
                    f"{_typescript_key(key)}{marker}: {_typescript_type(value)}"
                )
            return "{ " + "; ".join(pieces) + " }"
        return "Record<string, unknown>"
    return "unknown"


def _render_interface(name: str, schema: Mapping[str, Any]) -> list[str]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = [f"export interface {name} {{"]
    for key, value in properties.items():
        marker = "" if key in required else "?"
        lines.append(
            f"  {_typescript_key(key)}{marker}: {_typescript_type(value)};"
        )
    lines.append("}")
    return lines


def _typescript_declarations(
    dashboard_schema: Mapping[str, Any], country_schema: Mapping[str, Any]
) -> bytes:
    definitions: dict[str, Mapping[str, Any]] = {}
    for schema in (dashboard_schema, country_schema):
        for name, definition in schema.get("$defs", {}).items():
            existing = definitions.get(name)
            if existing is not None and existing != definition:
                raise ValueError(f"conflicting JSON Schema definition for {name}")
            definitions[name] = definition

    lines = [
        "// Generated by scripts/generate_dashboard_contract.py. Do not edit.",
        f"export const DASHBOARD_CONTRACT_VERSION = {json.dumps(CONTRACT_VERSION)} as const;",
        f"export const DASHBOARD_SCHEMA_VERSION = {SCHEMA_VERSION} as const;",
        "",
    ]
    for name in sorted(definitions):
        lines.extend(_render_interface(name, definitions[name]))
        lines.append("")

    lines.extend(_render_interface("DashboardSnapshot", dashboard_schema))
    lines.append("")
    lines.extend(_render_interface("CountrySnapshot", country_schema))
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _manifest(artifacts: Mapping[str, bytes]) -> bytes:
    artifact_rows = []
    bundle_hasher = hashlib.sha256()
    for path in sorted(artifacts):
        content = artifacts[path]
        digest = _sha256(content)
        artifact_rows.append(
            {"path": path, "bytes": len(content), "sha256": digest}
        )
        bundle_hasher.update(path.encode("utf-8"))
        bundle_hasher.update(b"\0")
        bundle_hasher.update(digest.encode("ascii"))
        bundle_hasher.update(b"\n")

    return _json_bytes(
        {
            "contract": "dashboard",
            "contract_version": CONTRACT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "generator": "scripts/generate_dashboard_contract.py",
            "pydantic_pin": PYDANTIC_PIN,
            "artifacts": artifact_rows,
            "bundle_sha256": bundle_hasher.hexdigest(),
        }
    )


def _contract_lock(artifacts: Mapping[str, bytes]) -> bytes:
    manifest = json.loads(artifacts["manifest.json"])
    return _json_bytes(
        {
            "contract": "dashboard",
            "current_version": CONTRACT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "manifest": f"{CONTRACT_VERSION}/manifest.json",
            "manifest_sha256": _sha256(artifacts["manifest.json"]),
            "bundle_sha256": manifest["bundle_sha256"],
        }
    )


def build_contract_artifacts() -> dict[str, bytes]:
    expected_version = PYDANTIC_PIN.split("==", 1)[1]
    if pydantic.__version__ != expected_version:
        raise RuntimeError(
            f"contract generation requires {PYDANTIC_PIN}; "
            f"found pydantic=={pydantic.__version__}"
        )

    dashboard_schema = _schema(DashboardSnapshot, "dashboard")
    country_schema = _schema(CountrySnapshot, "country")
    dashboard_fixture = validate_dashboard_snapshot(_dashboard_fixture())
    country_fixture = validate_country_snapshot(_country_fixture())

    artifacts = {
        "dashboard.schema.json": _json_bytes(dashboard_schema),
        "country.schema.json": _json_bytes(country_schema),
        "dashboard.fixture.json": _json_bytes(dashboard_fixture),
        "country.fixture.json": _json_bytes(country_fixture),
        "dashboard.types.ts": _typescript_declarations(
            dashboard_schema, country_schema
        ),
    }
    artifacts["manifest.json"] = _manifest(artifacts)
    return artifacts


def write_contract_bundle(bundle_dir: Path = BUNDLE_DIR) -> None:
    artifacts = build_contract_artifacts()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, content in artifacts.items():
        (bundle_dir / relative_path).write_bytes(content)
    (bundle_dir.parent / "contract.lock.json").write_bytes(
        _contract_lock(artifacts)
    )


def check_contract_bundle(bundle_dir: Path = BUNDLE_DIR) -> list[str]:
    expected = build_contract_artifacts()
    problems: list[str] = []

    actual_paths = {
        path.name for path in bundle_dir.iterdir() if path.is_file()
    } if bundle_dir.is_dir() else set()
    expected_paths = set(expected)
    for missing in sorted(expected_paths - actual_paths):
        problems.append(f"missing {missing}")
    for unexpected in sorted(actual_paths - expected_paths):
        problems.append(f"unexpected {unexpected}")

    for relative_path, expected_content in expected.items():
        path = bundle_dir / relative_path
        if path.is_file() and path.read_bytes() != expected_content:
            problems.append(f"stale {relative_path}")

    lock_path = bundle_dir.parent / "contract.lock.json"
    if not lock_path.is_file():
        problems.append("missing contract.lock.json")
    elif lock_path.read_bytes() != _contract_lock(expected):
        problems.append("stale contract.lock.json")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare committed artifacts without changing them",
    )
    args = parser.parse_args()

    if args.check:
        problems = check_contract_bundle()
        if problems:
            for problem in problems:
                print(problem)
            raise SystemExit(1)
        print(f"dashboard contract {CONTRACT_VERSION} is current")
        return

    write_contract_bundle()
    print(f"wrote dashboard contract {CONTRACT_VERSION} to {BUNDLE_DIR}")


if __name__ == "__main__":
    main()
