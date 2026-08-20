"""Audit migration head and safely adopt the frozen pre-Alembic baseline."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, inspect
from sqlalchemy.engine import Connection

from src.store.orm import CoreBase
from src.store.schema_baseline_0001 import BASELINE_METADATA, BASELINE_TABLE_NAMES

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPOSITORY_ROOT / "alembic.ini"
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / "migrations"
BASELINE_REVISION = "0001_core_baseline"
# Compatibility name for callers written during the bootstrap checkpoint.
# It intentionally means the frozen revision-0001 tables, not live CoreBase.
CORE_TABLE_NAMES = BASELINE_TABLE_NAMES
POST_BASELINE_MANAGED_TABLE_NAMES = frozenset(CoreBase.metadata.tables).difference(
    BASELINE_TABLE_NAMES
)


class CoreSchemaMismatch(RuntimeError):
    """Raised when a pre-Alembic database is not the recorded baseline."""


class CoreSchemaAlreadyVersioned(RuntimeError):
    """Raised rather than overwriting an existing, different revision."""


def _include_core_objects(  # noqa: ANN001
    object_, name: str | None, type_: str, reflected: bool, compare_to
) -> bool:
    """Limit Alembic's adoption comparison to frozen baseline tables.

    Check constraints are compared by ``_explicit_structure_differences``.
    Alembic 1.19 detects their presence by name but its default dialect
    implementation treats same-name expressions as equal without comparing
    the SQL text.
    """
    if type_ == "table" and reflected and name not in BASELINE_TABLE_NAMES:
        return False
    if type_ == "check_constraint":
        return False
    return True


def _include_head_objects(  # noqa: ANN001
    object_, name: str | None, type_: str, reflected: bool, compare_to
) -> bool:
    """Ignore legacy/unmanaged tables when comparing the live ORM head."""
    if type_ == "table" and reflected and name not in CoreBase.metadata.tables:
        return False
    return True


def _compact_sql(expression: str) -> str:
    """Remove insignificant SQL whitespace without changing string values."""
    compact: list[str] = []
    in_string = False
    in_identifier = False
    index = 0

    while index < len(expression):
        character = expression[index]

        if in_string:
            compact.append(character)
            if character == "'":
                next_is_quote = (
                    index + 1 < len(expression) and expression[index + 1] == "'"
                )
                if next_is_quote:
                    compact.append(expression[index + 1])
                    index += 1
                else:
                    in_string = False
        elif in_identifier:
            if character == '"':
                in_identifier = False
            else:
                # Preserve case: quoted "Decision" is not the same identifier
                # as the unquoted, lower-case decision column.
                compact.append(character)
        elif character == "'":
            in_string = True
            compact.append(character)
        elif character == '"':
            in_identifier = True
        elif not character.isspace():
            compact.append(character.lower())

        index += 1

    return "".join(compact)


def _strip_outer_parentheses(expression: str) -> str:
    """Remove only parentheses that wrap the complete expression."""
    result = expression
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        in_string = False
        wraps_complete_expression = True
        index = 0

        while index < len(result):
            character = result[index]
            if character == "'":
                if in_string and index + 1 < len(result) and result[index + 1] == "'":
                    index += 1
                else:
                    in_string = not in_string
            elif not in_string:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0 and index != len(result) - 1:
                        wraps_complete_expression = False
                        break
            index += 1

        if not wraps_complete_expression or depth != 0:
            break
        result = result[1:-1]

    return result


_SQL_LITERAL = r"'(?:''|[^'])*'"
_TEXT_CAST = r"(?:::(?:text|charactervarying|varchar))?"


def _parse_string_values(value_list: str) -> tuple[str, ...] | None:
    """Parse the string-only value lists used by the baseline decision check."""
    item_pattern = re.compile(rf"(?P<literal>{_SQL_LITERAL}){_TEXT_CAST}")
    values: list[str] = []
    position = 0

    while position < len(value_list):
        match = item_pattern.match(value_list, position)
        if match is None:
            return None

        literal = match.group("literal")[1:-1].replace("''", "'")
        values.append(literal)
        position = match.end()

        if position == len(value_list):
            break
        if value_list[position] != ",":
            return None
        position += 1

    return tuple(values)


def _normalize_check_expression(expression: str) -> tuple[Any, ...]:
    """Create a cross-dialect signature for a baseline check expression.

    SQLite preserves ``column IN (...)``. PostgreSQL may reflect that same
    predicate as ``column::text = ANY (ARRAY[...]::text[])``. The special
    case below recognizes only that exact structural rewrite and preserves
    the column and ordered literal values. Other checks use a strict compact
    SQL signature after redundant outer parentheses are removed.
    """
    compact = _compact_sql(expression)
    if compact.startswith("check(") and compact.endswith(")"):
        compact = compact[len("check(") : -1]
    compact = _strip_outer_parentheses(compact)

    in_match = re.fullmatch(
        rf"\(?(?P<column>[a-z_][a-z0-9_]*)\)?in\((?P<values>.*)\)",
        compact,
    )
    if in_match is not None:
        values = _parse_string_values(in_match.group("values"))
        if values is not None:
            return ("string_membership", in_match.group("column"), values)

    any_match = re.fullmatch(
        rf"\(?(?P<column>[a-z_][a-z0-9_]*)\)?{_TEXT_CAST}"
        rf"=any\(\(*array\[(?P<values>.*?)\]\)*"
        rf"(?:::(?:text|charactervarying|varchar)\[\])?\)*\)",
        compact,
    )
    if any_match is not None:
        values = _parse_string_values(any_match.group("values"))
        if values is not None:
            return ("string_membership", any_match.group("column"), values)

    return ("sql", _strip_outer_parentheses(compact))


def _check_signatures(connection: Connection, table_name: str) -> tuple[Any, ...]:
    inspector = inspect(connection)
    reflected_checks = inspector.get_check_constraints(table_name)
    signatures = []
    for check in reflected_checks:
        reflected_options = check.get("dialect_options", {})
        dialect_options = tuple(
            sorted(
                (str(key), repr(value))
                for key, value in reflected_options.items()
            )
        )
        signatures.append(
            (
                str(check.get("name") or "<unnamed>"),
                _normalize_check_expression(str(check.get("sqltext") or "")),
                dialect_options,
            )
        )
    return tuple(sorted(signatures, key=repr))


def _expected_check_signatures(
    connection: Connection,
    metadata,
    table_name: str,
) -> tuple[Any, ...]:  # noqa: ANN001
    table = metadata.tables[table_name]
    signatures = []
    for constraint in table.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        compiled_expression = str(
            constraint.sqltext.compile(
                dialect=connection.dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        signatures.append(
            (
                str(constraint.name or "<unnamed>"),
                _normalize_check_expression(compiled_expression),
                (),
            )
        )
    return tuple(sorted(signatures, key=repr))


def _explicit_structure_differences(
    connection: Connection,
    metadata=BASELINE_METADATA,  # noqa: ANN001
    *,
    compare_checks: bool = True,
    detect_post_baseline_tables: bool = True,
) -> list[Any]:
    """Compare structures that Alembic autogenerate does not compare fully."""
    inspector = inspect(connection)
    reflected_tables = set(inspector.get_table_names())
    differences: list[Any] = []

    # Unlike raw_articles/processed_articles or an extension-owned table,
    # these names are known to belong to a revision after 0001. If one exists
    # without Alembic history, stamping 0001 would make the later create-table
    # revision collide with it.
    if detect_post_baseline_tables:
        premature_managed_tables = sorted(
            reflected_tables.intersection(POST_BASELINE_MANAGED_TABLE_NAMES)
        )
        if premature_managed_tables:
            differences.append(
                (
                    "post_baseline_managed_tables_present",
                    tuple(premature_managed_tables),
                )
            )

    for table_name, table in metadata.tables.items():
        if table_name not in reflected_tables:
            # Alembic already reports the missing table. Avoid reflection
            # errors and duplicate noise here.
            continue

        expected_primary_key = tuple(column.name for column in table.primary_key.columns)
        reflected_primary_key = inspector.get_pk_constraint(table_name)
        actual_primary_key = tuple(reflected_primary_key.get("constrained_columns") or ())
        if actual_primary_key != expected_primary_key:
            differences.append(
                (
                    "primary_key_mismatch",
                    table_name,
                    {"expected": expected_primary_key, "actual": actual_primary_key},
                )
            )

        if compare_checks:
            expected_checks = _expected_check_signatures(
                connection,
                metadata,
                table_name,
            )
            actual_checks = _check_signatures(connection, table_name)
            if actual_checks != expected_checks:
                differences.append(
                    (
                        "check_constraint_mismatch",
                        table_name,
                        {"expected": expected_checks, "actual": actual_checks},
                    )
                )

    return differences


def make_alembic_config(
    *,
    connection: Connection | None = None,
    database_url: str | None = None,
) -> Config:
    """Build one location-stable Alembic config for tools and tests."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    if connection is not None:
        config.attributes["connection"] = connection
    if database_url is not None:
        # ConfigParser treats percent signs in URLs as interpolation. Passing
        # this via attributes keeps percent-encoded credentials untouched.
        config.attributes["database_url"] = database_url
    return config


def audit_core_schema(connection: Connection) -> list[Any]:
    """Return differences from the frozen revision-0001 adoption schema.

    Alembic compares tables, columns, types, nullability, defaults, foreign
    keys, unique constraints, and indexes. It does not compare primary keys,
    and its default same-name check comparison does not compare expressions.
    Explicit normalized comparisons close those gaps. The table filter uses
    immutable baseline metadata rather than live ``CoreBase``, so later ORM
    additions do not redefine what it means to stamp revision 0001.

    Tables outside the frozen baseline remain out of scope, preserving legacy
    storage. PostgreSQL 16 CI remains required because reflection is dialect
    behavior; SQLite alone cannot authorize a production stamp.
    """
    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": BASELINE_METADATA,
            "include_object": _include_core_objects,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    differences = list(compare_metadata(context, BASELINE_METADATA))
    differences.extend(_explicit_structure_differences(connection))
    return differences


def audit_head_schema(connection: Connection) -> list[Any]:
    """Return drift between a migrated database and the live ``CoreBase``.

    This is deliberately separate from ``audit_core_schema``: the latter asks
    whether an unversioned database may truthfully adopt revision 0001, while
    this function asks whether ``upgrade head`` produced today's ORM schema.
    Alembic compares live checks by name and all of its normal schema object
    categories; the explicit supplement also verifies primary-key columns.
    The stricter normalized check-expression comparison stays baseline-only
    until it has PostgreSQL fixtures for every later expression shape.
    """
    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": CoreBase.metadata,
            "include_object": _include_head_objects,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    differences = list(compare_metadata(context, CoreBase.metadata))
    differences.extend(
        _explicit_structure_differences(
            connection,
            CoreBase.metadata,
            compare_checks=False,
            detect_post_baseline_tables=False,
        )
    )
    return differences


def format_schema_differences(differences: list[Any]) -> str:
    """Render Alembic's nested difference values without hiding detail."""
    return "\n".join(f"- {difference!r}" for difference in differences)


def current_revisions(connection: Connection) -> tuple[str, ...]:
    context = MigrationContext.configure(connection)
    return tuple(context.get_current_heads())


def stamp_existing_core_schema(connection: Connection) -> bool:
    """Audit then stamp a matching pre-Alembic schema at the baseline.

    Returns ``True`` when a stamp was written and ``False`` when the database
    was already stamped at the baseline. No DDL from the baseline revision is
    executed, and a different existing revision is never overwritten.
    """
    differences = audit_core_schema(connection)
    if differences:
        details = format_schema_differences(differences)
        raise CoreSchemaMismatch(
            "Core schema does not match the pre-Alembic baseline; refusing "
            f"to stamp.\n{details}"
        )

    revisions = current_revisions(connection)
    if revisions == (BASELINE_REVISION,):
        return False
    if revisions:
        joined = ", ".join(revisions)
        raise CoreSchemaAlreadyVersioned(
            f"Database already carries Alembic revision(s) {joined}; refusing to overwrite history."
        )

    config = make_alembic_config(connection=connection)
    command.stamp(config, BASELINE_REVISION)

    stamped_revisions = current_revisions(connection)
    if stamped_revisions != (BASELINE_REVISION,):
        raise RuntimeError(
            "Alembic stamp did not produce the expected baseline revision "
            f"{BASELINE_REVISION!r}: {stamped_revisions!r}"
        )
    return True
