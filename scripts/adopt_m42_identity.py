"""Audit or idempotently adopt durable M4.2a identity mappings.

Run this only after the additive ``0004_evidence_identity`` schema revision.
The check mode writes nothing.  Apply first revalidates every old source span,
then creates document/evidence mappings and durable resolution constraints in
one database transaction.  A failed validation leaves no partial adoption.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.ontology import Ontology
from src.store.blob import get_blob_store
from src.store.identity import (
    IdentityAdoptionError,
    apply_identity_adoption,
    canonical_language,
    plan_identity_adoption,
)
from src.store.schema_rollout import CoreSchemaRolloutError, verify_core_schema_at_head

_ONTOLOGY_PATH = REPOSITORY_ROOT / "config" / "ontology.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit or idempotent adoption of M4.2 durable document/evidence "
            "identity and human constraint mappings."
        )
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults only to the explicit DATABASE_URL environment variable.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate and print JSON without writing.")
    mode.add_argument("--apply", action="store_true", help="Write a validated, resumable adoption.")
    parser.add_argument(
        "--extractor-language",
        action="append",
        default=[],
        metavar="EXTRACTOR=LANGUAGE",
        help=(
            "Explicit language for a legacy extractor name; repeat for multiple names. "
            "Example: gazetteer_extractor=ar"
        ),
    )
    parser.add_argument(
        "--default-language",
        default=None,
        help=(
            "Explicit fallback BCP-47 language for legacy extractor names not otherwise mapped. "
            "Omit to report those rows rather than guessing."
        ),
    )
    return parser


def parse_extractor_languages(values: list[str]) -> dict[str, str]:
    """Parse the CLI's declarative extractor-name -> language mapping."""

    result: dict[str, str] = {}
    for value in values:
        name, separator, language = value.partition("=")
        name = name.strip()
        language = language.strip()
        if value.count("=") != 1 or not separator or not name or not language:
            raise ValueError(
                "--extractor-language entries must use EXTRACTOR=LANGUAGE"
            )
        if name in result:
            raise ValueError(f"language for extractor {name!r} was supplied more than once")
        result[name] = canonical_language(language)
    return result


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error(
            "set DATABASE_URL or pass --database-url; identity adoption will not guess a database target"
        )

    try:
        extractor_languages = parse_extractor_languages(args.extractor_language)
        default_language = (
            canonical_language(args.default_language)
            if args.default_language is not None
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))

    mode = "apply" if args.apply else "check"
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            verify_core_schema_at_head(connection)

        ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
        kwargs = {
            "blob_store": get_blob_store(),
            "extractor_languages": extractor_languages,
            "default_language": default_language,
            "valid_object_types": ontology.object_type_names(),
        }
        with Session(engine) as session:
            if args.check:
                report = plan_identity_adoption(session, **kwargs)
                _print_json(report.as_dict(mode=mode))
                return 0 if report.ready else 1

            try:
                with session.begin():
                    report = apply_identity_adoption(session, **kwargs)
            except IdentityAdoptionError as exc:
                _print_json(exc.report.as_dict(mode=mode))
                return 1
            _print_json(report.as_dict(mode=mode))
            return 0
    except CoreSchemaRolloutError as exc:
        _print_json(
            {
                "mode": mode,
                "ready": False,
                "errors": [
                    {
                        "kind": "schema_not_at_head",
                        "row_id": 0,
                        "detail": str(exc),
                    }
                ],
            }
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
