"""Database engine and session lifecycle helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.settings import SETTINGS
from src.database.models import Base


_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def get_engine(database_url: str | None = None) -> Engine:
    """Return singleton SQLAlchemy engine."""
    global _ENGINE
    if _ENGINE is None or database_url:
        _ENGINE = create_engine(database_url or SETTINGS.database_url, pool_pre_ping=True)
    return _ENGINE


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """Return singleton sessionmaker bound to active engine."""
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None or database_url:
        _SESSION_FACTORY = sessionmaker(bind=get_engine(database_url), autoflush=False, autocommit=False)
    return _SESSION_FACTORY


@contextmanager
def get_db_session(database_url: str | None = None) -> Generator[Session, None, None]:
    """Provide transactional scope for DB operations."""
    session = get_session_factory(database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str | None = None) -> None:
    """Create ORM tables if they do not already exist."""
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)
