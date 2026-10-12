"""Read a bounded local fixture/snapshot and print private evidence JSON.

Run with ``python -m src.search.evidence_cli --snapshot PATH --query NAME``.
This command intentionally has no database, URL, output-file, or provider mode.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from src.core.models import Document, Entity, ExtractorVersion, Mention
from src.search.evidence import (
    CachedEvidenceTranslation,
    EntityMention,
    EvidenceCorpus,
    EvidenceInputError,
    SearchEntity,
    SourceDocument,
    VerifiedAlias,
    search_evidence,
    validate_corpus,
)

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_COLLECTIONS = {"documents", "entities", "mentions", "memberships", "extractor_versions", "aliases", "translations"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise EvidenceInputError(f"non-finite JSON number: {value}")


def load_snapshot(path: Path) -> EvidenceCorpus:
    """Load local bytes only; validate every row before returning the snapshot."""
    with path.open("rb") as handle:
        payload = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise EvidenceInputError("snapshot exceeds the 8 MiB input limit")
    data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(data, dict):
        raise EvidenceInputError("snapshot must be a JSON object")
    allowed = _COLLECTIONS | {"schema_version", "data_kind"}
    if set(data) - allowed:
        raise EvidenceInputError("unknown snapshot fields: " + ", ".join(sorted(set(data) - allowed)))
    if data.get("schema_version") != "offline-evidence-v1":
        raise EvidenceInputError("unsupported snapshot schema_version")
    if "data_kind" not in data:
        raise EvidenceInputError("snapshot must explicitly state data_kind")
    for name in _COLLECTIONS:
        if name not in data and name not in {"aliases", "translations"}:
            raise EvidenceInputError(f"snapshot is missing {name}")
        rows = data.get(name, [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise EvidenceInputError(f"{name} must be an array of objects")
    documents = []
    entities = []
    try:
        for source in data["documents"]:
            row = dict(source)
            language = row.pop("language")
            sha256 = row.pop("text_sha256")
            length = row.pop("text_length")
            documents.append(SourceDocument(Document(**row), language, sha256, length))
        for source in data["entities"]:
            row = dict(source)
            language = row.pop("language")
            row.setdefault("properties", {})
            entities.append(SearchEntity(Entity(**row), language))
        corpus = EvidenceCorpus(
            documents=tuple(documents),
            entities=tuple(entities),
            mentions=tuple(Mention(**row) for row in data["mentions"]),
            memberships=tuple(EntityMention(**row) for row in data["memberships"]),
            extractor_versions=tuple(ExtractorVersion(**row) for row in data["extractor_versions"]),
            aliases=tuple(VerifiedAlias(**row) for row in data.get("aliases", [])),
            translations=tuple(CachedEvidenceTranslation(**row) for row in data.get("translations", [])),
            data_kind=data["data_kind"],
        )
    except (KeyError, TypeError) as exc:
        raise EvidenceInputError(f"invalid snapshot row: {exc}") from exc
    validate_corpus(corpus)
    return corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Local UTF-8 JSON snapshot; never a URL")
    parser.add_argument("--query", required=True, help="Arabic or Latin-script entity query")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--evidence-per-entity", type=int, default=3)
    parser.add_argument("--span-characters", type=int, default=280)
    parser.add_argument("--target-language", default="en")
    args = parser.parse_args(argv)
    try:
        result = search_evidence(
            load_snapshot(args.snapshot), args.query, limit=args.limit,
            evidence_per_entity=args.evidence_per_entity, span_characters=args.span_characters,
            target_language=args.target_language,
        )
    except (OSError, ValueError) as exc:
        print(f"Evidence input error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
