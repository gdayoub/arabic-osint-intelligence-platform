"""Alembic environment for the active core schema only.

The legacy article metadata is intentionally not imported. This prevents
autogenerate from proposing changes to frozen tables that Alembic does not
own. See ADR 0017.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from src.store.orm import CoreBase

config = context.config
target_metadata = CoreBase.metadata
CORE_TABLE_NAMES = frozenset(target_metadata.tables)


def _database_url() -> str:
    """Use only a programmatic, configured, or explicit environment URL."""
    explicit_url = config.attributes.get("database_url")
    if explicit_url:
        return str(explicit_url)

    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url

    environment_url = os.getenv("DATABASE_URL")
    if environment_url:
        return environment_url

    raise RuntimeError(
        "Migration target is missing. Set DATABASE_URL or pass an explicit "
        "programmatic database_url; Alembic will not guess a database."
    )


def _include_object(  # noqa: ANN001
    object_, name: str | None, type_: str, reflected: bool, compare_to
) -> bool:
    """Keep Alembic authoritative over CoreBase and blind to other tables."""
    if type_ == "table" and reflected and name not in CORE_TABLE_NAMES:
        return False
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _configure(supplied_connection)
        return

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _configure(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
