"""Print the read-only M4.2b history view for one durable stable entity.

This intentionally lives outside ``main.py`` while M4.2b is observe-only.
The command performs no resolver, adoption, or state-coordination write; it
only reads the immutable stable-entity projection after verifying the schema
is at the repository head.
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

from src.resolve.stable_entities import stable_entity_history
from src.store.schema_rollout import CoreSchemaRolloutError, verify_core_schema_at_head


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read one M4.2b stable entity's immutable resolver history."
    )
    parser.add_argument("stable_uid", help="UUID from stable_entities.stable_uid")
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults only to the explicit DATABASE_URL environment variable.",
    )
    parser.add_argument(
        "--as-of-sequence",
        type=int,
        default=None,
        help="Read one exact observed generation sequence instead of the active one.",
    )
    return parser


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error(
            "set DATABASE_URL or pass --database-url; history will not guess a database target"
        )

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            verify_core_schema_at_head(connection)
        with Session(engine) as session:
            history = stable_entity_history(
                session,
                args.stable_uid,
                as_of_sequence=args.as_of_sequence,
            )
            if history is None:
                _print_json(
                    {
                        "found": False,
                        "stable_uid": args.stable_uid,
                        "error": "stable_entity_not_found",
                    }
                )
                return 1
            _print_json({"found": True, "history": history.as_dict()})
            return 0
    except (CoreSchemaRolloutError, ValueError) as exc:
        _print_json(
            {
                "found": False,
                "stable_uid": args.stable_uid,
                "error": str(exc),
            }
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
