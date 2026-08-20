"""Fail-closed planning and application of explicit core-schema upgrades.

The scheduled pipeline only verifies schema state.  This module is the one
place that is allowed to advance a versioned production schema, and it does
so only across the exact revision boundary named by the operator.
"""

from __future__ import annotations

from dataclasses import dataclass

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from src.store.orm import CoreBase
from src.store.schema_migrations import (
    BASELINE_REVISION,
    audit_core_schema,
    audit_head_schema,
    current_revisions,
    format_schema_differences,
    make_alembic_config,
)

BASE_REVISION = "base"
LEGACY_TABLE_NAMES = ("raw_articles", "processed_articles")


class CoreSchemaRolloutError(RuntimeError):
    """Raised before a schema rollout can cross an unverified boundary."""


@dataclass(frozen=True)
class CoreSchemaUpgradePlan:
    """The exact, linear migration path approved for one rollout."""

    current_revision: str
    target_revision: str
    revisions_to_apply: tuple[str, ...]


def repository_head_revision() -> str:
    """Return the repository's only migration head, refusing branch ambiguity."""
    script = ScriptDirectory.from_config(make_alembic_config())
    heads = tuple(script.get_heads())
    if len(heads) != 1:
        joined = ", ".join(heads) if heads else "none"
        raise CoreSchemaRolloutError(
            "Core migration history must have exactly one head before rollout; "
            f"found {joined}."
        )
    return heads[0]


def schema_upgrade_confirmation(current_revision: str, target_revision: str) -> str:
    """Return the exact human confirmation phrase for a revision boundary."""
    return f"UPGRADE {current_revision} TO {target_revision}"


def _revision_path(
    current_revision: str,
    target_revision: str,
) -> tuple[str, ...]:
    """Return a forward-only, single-parent path from current to target."""
    script = ScriptDirectory.from_config(make_alembic_config())
    target = script.get_revision(target_revision)
    if target is None or target.revision != target_revision:
        raise CoreSchemaRolloutError(
            f"Target revision {target_revision!r} is not an exact revision id."
        )

    if current_revision != BASE_REVISION:
        current = script.get_revision(current_revision)
        if current is None or current.revision != current_revision:
            raise CoreSchemaRolloutError(
                f"Current revision {current_revision!r} is not an exact revision id."
            )

    reverse_path: list[str] = []
    cursor = target
    visited: set[str] = set()
    while cursor.revision != current_revision:
        if cursor.revision in visited:
            raise CoreSchemaRolloutError("Core migration history contains a cycle.")
        visited.add(cursor.revision)
        reverse_path.append(cursor.revision)

        down_revision = cursor.down_revision
        if down_revision is None:
            if current_revision == BASE_REVISION:
                break
            raise CoreSchemaRolloutError(
                f"Revision {target_revision} does not descend from {current_revision}."
            )
        if not isinstance(down_revision, str):
            raise CoreSchemaRolloutError(
                "Branched or merged migration history requires a dedicated rollout plan."
            )

        parent = script.get_revision(down_revision)
        if parent is None:
            raise CoreSchemaRolloutError(
                f"Migration {cursor.revision} names missing parent {down_revision}."
            )
        cursor = parent

    path = tuple(reversed(reverse_path))
    if not path:
        raise CoreSchemaRolloutError(
            f"Database is already at target revision {target_revision}; use schema verification."
        )
    return path


def _expected_current_heads(expected_current: str) -> tuple[str, ...]:
    if expected_current == BASE_REVISION:
        return ()
    return (expected_current,)


def _verify_empty_core_schema(connection: Connection) -> None:
    table_names = set(inspect(connection).get_table_names())
    unversioned_core_tables = sorted(table_names.intersection(CoreBase.metadata.tables))
    if unversioned_core_tables:
        joined = ", ".join(unversioned_core_tables)
        raise CoreSchemaRolloutError(
            "An unversioned database already contains migration-owned core tables: "
            f"{joined}. Audit and stamp a known baseline instead of treating it as empty."
        )


def plan_core_schema_upgrade(
    connection: Connection,
    *,
    expected_current: str,
    expected_target: str,
) -> CoreSchemaUpgradePlan:
    """Verify and describe an exact production revision transition.

    Planning is read-only.  The repository target must be its sole head, the
    database must be exactly at the named current revision, and the migration
    graph between them must be linear and forward-only.
    """
    repository_head = repository_head_revision()
    if expected_target != repository_head:
        raise CoreSchemaRolloutError(
            f"Expected target {expected_target!r} is not repository head "
            f"{repository_head!r}."
        )

    actual_heads = current_revisions(connection)
    expected_heads = _expected_current_heads(expected_current)
    if actual_heads != expected_heads:
        actual_text = ", ".join(actual_heads) if actual_heads else BASE_REVISION
        raise CoreSchemaRolloutError(
            f"Database revision is {actual_text}; expected exactly {expected_current}."
        )

    if expected_current == BASE_REVISION:
        _verify_empty_core_schema(connection)
    elif expected_current == BASELINE_REVISION:
        differences = audit_core_schema(connection)
        if differences:
            raise CoreSchemaRolloutError(
                "Revision 0001 is recorded but its frozen schema has drifted; "
                "refusing to migrate.\n"
                + format_schema_differences(differences)
            )

    path = _revision_path(expected_current, expected_target)
    return CoreSchemaUpgradePlan(
        current_revision=expected_current,
        target_revision=expected_target,
        revisions_to_apply=path,
    )


def _legacy_table_counts(connection: Connection) -> tuple[tuple[str, int], ...]:
    """Snapshot frozen legacy tables so a core migration cannot alter them."""
    table_names = set(inspect(connection).get_table_names())
    counts: list[tuple[str, int]] = []
    quote = connection.dialect.identifier_preparer.quote
    for table_name in LEGACY_TABLE_NAMES:
        if table_name not in table_names:
            continue
        count = connection.scalar(text(f"SELECT count(*) FROM {quote(table_name)}"))
        counts.append((table_name, int(count or 0)))
    return tuple(counts)


def verify_core_schema_at_head(connection: Connection) -> str:
    """Require the database revision and reflected schema to match code head."""
    expected_head = repository_head_revision()
    actual_heads = current_revisions(connection)
    if actual_heads != (expected_head,):
        actual_text = ", ".join(actual_heads) if actual_heads else BASE_REVISION
        raise CoreSchemaRolloutError(
            f"Core schema is {actual_text}; this checkout requires {expected_head}. "
            "No pipeline write is safe until the explicit schema rollout succeeds."
        )

    differences = audit_head_schema(connection)
    if differences:
        raise CoreSchemaRolloutError(
            f"Core schema at revision {expected_head} differs from the ORM head.\n"
            + format_schema_differences(differences)
        )
    return expected_head


def apply_core_schema_upgrade(
    connection: Connection,
    *,
    expected_current: str,
    expected_target: str,
    confirmation: str,
) -> CoreSchemaUpgradePlan:
    """Apply one confirmed migration path and verify it before commit."""
    expected_confirmation = schema_upgrade_confirmation(
        expected_current,
        expected_target,
    )
    if confirmation != expected_confirmation:
        raise CoreSchemaRolloutError(
            "Schema confirmation did not match exactly. Required: "
            f"{expected_confirmation}"
        )

    plan = plan_core_schema_upgrade(
        connection,
        expected_current=expected_current,
        expected_target=expected_target,
    )
    legacy_counts_before = _legacy_table_counts(connection)

    command.upgrade(
        make_alembic_config(connection=connection),
        expected_target,
    )

    applied_head = verify_core_schema_at_head(connection)
    if applied_head != expected_target:
        raise CoreSchemaRolloutError(
            f"Upgrade reached {applied_head}, not approved target {expected_target}."
        )

    legacy_counts_after = _legacy_table_counts(connection)
    if legacy_counts_after != legacy_counts_before:
        raise CoreSchemaRolloutError(
            "A core migration changed the presence or row count of frozen legacy "
            f"tables: before={legacy_counts_before!r}, after={legacy_counts_after!r}."
        )
    return plan
