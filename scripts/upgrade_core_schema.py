"""Plan or explicitly apply one production core-schema revision boundary."""

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
    apply_core_schema_upgrade,
    plan_core_schema_upgrade,
    schema_upgrade_confirmation,
    verify_core_schema_at_head,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only plan by default. --apply requires the exact current and "
            "target revisions plus a typed confirmation."
        )
    )
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--expected-target", required=True)
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults only to the explicit DATABASE_URL environment variable.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirmation",
        default=None,
        help="For apply, type: UPGRADE <expected-current> TO <expected-target>",
    )
    parser.add_argument(
        "--recovery-reference",
        default=None,
        help="For apply, record the Neon branch, PITR point, or recovery ticket prepared first.",
    )
    return parser


def _validated_recovery_reference(value: str | None) -> str:
    if value is None or not value.strip():
        raise CoreSchemaRolloutError(
            "--apply requires --recovery-reference naming the prepared restore point."
        )
    reference = value.strip()
    if len(reference) > 200 or any(character in reference for character in "\r\n"):
        raise CoreSchemaRolloutError(
            "Recovery reference must be one non-empty line of at most 200 characters."
        )
    return reference


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error(
            "set DATABASE_URL or pass --database-url; schema rollout will not "
            "guess a database target"
        )

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        if not args.apply:
            with engine.connect() as connection:
                plan = plan_core_schema_upgrade(
                    connection,
                    expected_current=args.expected_current,
                    expected_target=args.expected_target,
                )
            path = " -> ".join(plan.revisions_to_apply)
            confirmation = schema_upgrade_confirmation(
                plan.current_revision,
                plan.target_revision,
            )
            print(
                f"Read-only schema plan passed: {plan.current_revision} -> "
                f"{path}."
            )
            print(f"Required apply confirmation: {confirmation}")
            return 0

        recovery_reference = _validated_recovery_reference(args.recovery_reference)
        with engine.begin() as connection:
            plan = apply_core_schema_upgrade(
                connection,
                expected_current=args.expected_current,
                expected_target=args.expected_target,
                confirmation=args.confirmation or "",
            )

        # Reconnect after commit. This proves the revision and reflected ORM
        # shape are durable, rather than only visible inside the DDL transaction.
        with engine.connect() as connection:
            verified_revision = verify_core_schema_at_head(connection)

        path = " -> ".join(plan.revisions_to_apply)
        print(
            f"Core schema upgrade committed and verified: {plan.current_revision} "
            f"-> {path}."
        )
        print(f"Verified revision: {verified_revision}.")
        print(f"Recorded recovery reference: {recovery_reference}")
        return 0
    except CoreSchemaRolloutError as exc:
        print(f"Core schema rollout REFUSED. {exc}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
