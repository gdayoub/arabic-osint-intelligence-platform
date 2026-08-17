"""Engine and session lifecycle for the core schema.

Mirrors src/database/db.py's pattern but binds to CoreBase (src/store/orm.py)
instead of the article Base — the two schemas coexist in the same database.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import SETTINGS
from src.store.orm import CoreBase

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
    """Create core schema tables if they do not already exist."""
    engine = get_core_engine(database_url)
    CoreBase.metadata.create_all(bind=engine)
