"""Fail before a writer runs against a core schema that does not match code."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sqlalchemy import create_engine

from src.store.schema_rollout import (
    CoreSchemaRolloutError,
    verify_core_schema_at_head,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify that a database exactly matches this checkout's core schema head."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults only to the explicit DATABASE_URL environment variable.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error(
            "set DATABASE_URL or pass --database-url; schema verification will "
            "not guess a database target"
        )

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            revision = verify_core_schema_at_head(connection)
        print(f"Core schema verification passed at {revision}.")
        return 0
    except CoreSchemaRolloutError as exc:
        print(f"Core schema verification FAILED. {exc}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
