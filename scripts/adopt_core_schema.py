"""Read-only audit, with an explicit opt-in to stamp a pre-Alembic schema."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ``python scripts/adopt_core_schema.py`` places ``scripts/`` rather than the
# repository root on sys.path. Add the root explicitly so the documented
# command works without relying on a caller-provided PYTHONPATH.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sqlalchemy import create_engine

from src.store.schema_migrations import (
    BASELINE_REVISION,
    CoreSchemaAlreadyVersioned,
    CoreSchemaMismatch,
    audit_core_schema,
    current_revisions,
    format_schema_differences,
    stamp_existing_core_schema,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the frozen pre-Alembic core baseline and optionally stamp its "
            "baseline revision."
        )
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults only to the explicit DATABASE_URL environment variable.",
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="After a clean audit, record the baseline revision without running its DDL.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error(
            "set DATABASE_URL or pass --database-url; schema adoption will "
            "not guess a database target"
        )
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.begin() as connection:
            differences = audit_core_schema(connection)
            if differences:
                print("Core schema audit FAILED. Nothing was stamped.")
                print(format_schema_differences(differences))
                return 1

            revisions = current_revisions(connection)
            revision_text = ", ".join(revisions) if revisions else "unversioned"
            print(f"Core schema audit passed. Current revision: {revision_text}.")

            if not args.stamp:
                print(
                    "Read-only audit complete. Re-run with --stamp to record "
                    f"{BASELINE_REVISION}."
                )
                return 0

            changed = stamp_existing_core_schema(connection)
            if changed:
                print(f"Stamped existing core schema at {BASELINE_REVISION}.")
            else:
                print(f"Core schema was already stamped at {BASELINE_REVISION}.")
            return 0
    except (CoreSchemaMismatch, CoreSchemaAlreadyVersioned) as exc:
        print(str(exc))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
