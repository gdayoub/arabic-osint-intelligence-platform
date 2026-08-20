"""Engine and session lifecycle for the core schema.

Mirrors src/database/db.py's pattern but binds to CoreBase (src/store/orm.py)
instead of the article Base — the two schemas coexist in the same database.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import SETTINGS
from src.store.orm import CoreBase
from src.store.schema_migrations import current_revisions
from src.store.schema_rollout import (
    BASE_REVISION,
    CoreSchemaRolloutError,
    apply_core_schema_upgrade,
    repository_head_revision,
    schema_upgrade_confirmation,
    verify_core_schema_at_head,
)

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def get_core_engine(database_url: str | None = None) -> Engine:
    global _ENGINE
    if _ENGINE is None or database_url:
        url = database_url or SETTINGS.database_url
        _ENGINE = create_engine(
            url,
            pool_pre_ping=True,
            # Default json.dumps escapes non-ASCII to \uXXXX, tripling the
            # storage cost of Arabic text in JSON columns for no benefit —
            # Postgres and SQLite both store UTF-8 natively.
            json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
        )
        if url.startswith("sqlite"):
            # SQLite ignores foreign keys unless told otherwise per-connection.
            # Without this, a bad extractor_version_id or document_id on a
            # provenance row would insert silently instead of failing (P1).
            @event.listens_for(_ENGINE, "connect")
            def _enable_sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _ENGINE


def get_core_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None or database_url:
        _SESSION_FACTORY = sessionmaker(bind=get_core_engine(database_url), autoflush=False, autocommit=False)
    return _SESSION_FACTORY


@contextmanager
def get_core_session(database_url: str | None = None) -> Generator[Session, None, None]:
    session = get_core_session_factory(database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_core_db(database_url: str | None = None) -> None:
    """Initialize an empty core schema through Alembic, or verify its head.

    This command intentionally refuses to advance an existing versioned
    database. Production upgrades require the explicit rollout command and
    typed revision boundary; ``create_all()`` is never a migration path.
    """
    engine = get_core_engine(database_url)
    with engine.begin() as connection:
        revisions = current_revisions(connection)
        if revisions:
            verify_core_schema_at_head(connection)
            return

        table_names = set(inspect(connection).get_table_names())
        existing_core_tables = sorted(
            table_names.intersection(CoreBase.metadata.tables)
        )
        if existing_core_tables:
            joined = ", ".join(existing_core_tables)
            raise CoreSchemaRolloutError(
                "Core schema is unversioned but already contains managed tables "
                f"({joined}). Run the frozen baseline audit/adoption path; "
                "initialization will not guess or stamp history."
            )

        target = repository_head_revision()
        apply_core_schema_upgrade(
            connection,
            expected_current=BASE_REVISION,
            expected_target=target,
            confirmation=schema_upgrade_confirmation(BASE_REVISION, target),
        )
