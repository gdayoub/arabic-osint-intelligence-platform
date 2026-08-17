"""Shared fixtures for core-schema unit tests.

Each test gets its own in-memory SQLite database. StaticPool keeps the same
connection alive for the whole engine (plain in-memory SQLite would otherwise
hand out a fresh, empty database on every checkout, since ":memory:" is
per-connection). Foreign keys are off by default in SQLite, so we turn them
on explicitly — without that, an invalid extractor_version_id would insert
silently instead of raising, defeating the point of testing P1/P2 here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.ontology import Ontology
from src.store.blob import BlobStore, LocalDiskBlobStore
from src.store.orm import CoreBase


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    CoreBase.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture()
def ontology() -> Ontology:
    repo_root = Path(__file__).resolve().parents[2]
    return Ontology.from_yaml(repo_root / "config" / "ontology.yaml")


@pytest.fixture()
def blob_store(tmp_path) -> BlobStore:
    """A fresh on-disk blob store per test. tmp_path is a pytest builtin
    fixture (a unique Path per test), so there's no cleanup code to write.
    """
    return LocalDiskBlobStore(root=tmp_path / "blobs")
