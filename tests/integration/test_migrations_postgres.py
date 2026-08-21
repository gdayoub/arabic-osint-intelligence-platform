"""Authoritative migration checks against a disposable PostgreSQL server."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from src.database.models import Base as LegacyBase
from src.store.orm import CoreBase
from src.store.schema_baseline_0001 import BASELINE_METADATA, BASELINE_TABLE_NAMES
from src.store.schema_migrations import (
    BASELINE_REVISION,
    CoreSchemaMismatch,
    audit_core_schema,
    audit_head_schema,
    current_revisions,
    make_alembic_config,
    stamp_existing_core_schema,
)

ADMIN_DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_DATABASE_URL,
    reason="MIGRATION_TEST_DATABASE_URL is required for PostgreSQL migration checks",
)


@contextmanager
def _disposable_database():
    """Create and remove one isolated database on the CI PostgreSQL service."""
    assert ADMIN_DATABASE_URL is not None
    database_name = f"osint_migration_{uuid4().hex}"
    admin_engine = create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    database_url = str(make_url(ADMIN_DATABASE_URL).set(database=database_name))

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    try:
        yield database_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE "{database_name}"'))
        admin_engine.dispose()


def test_postgres_empty_database_upgrades_to_exact_core_head():
    with _disposable_database() as database_url:
        config = make_alembic_config(database_url=database_url)
        expected_head = ScriptDirectory.from_config(config).get_current_head()
        command.upgrade(config, "head")

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert current_revisions(connection) == (expected_head,)
                assert audit_head_schema(connection) == []
                assert set(inspect(connection).get_table_names()) == set(
                    CoreBase.metadata.tables
                ) | {"alembic_version"}
        finally:
            engine.dispose()


def test_postgres_empty_database_upgrades_to_exact_adoption_baseline():
    with _disposable_database() as database_url:
        command.upgrade(
            make_alembic_config(database_url=database_url),
            BASELINE_REVISION,
        )

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert current_revisions(connection) == (BASELINE_REVISION,)
                assert audit_core_schema(connection) == []
                assert set(inspect(connection).get_table_names()) == (
                    set(BASELINE_TABLE_NAMES) | {"alembic_version"}
                )
        finally:
            engine.dispose()


def test_postgres_existing_schema_is_stamped_without_touching_rows():
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        LegacyBase.metadata.create_all(engine)
        BASELINE_METADATA.create_all(engine)
        collected_at = datetime(2026, 8, 20, tzinfo=timezone.utc)

        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO documents "
                        "(source, content_hash, collected_at, retracted) "
                        "VALUES (:source, :content_hash, :collected_at, :retracted)"
                    ),
                    {
                        "source": "postgres-adoption-test",
                        "content_hash": "existing-core-row",
                        "collected_at": collected_at,
                        "retracted": False,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO raw_articles "
                        "(source, title, body, url, collected_at, content_hash) "
                        "VALUES (:source, :title, :body, :url, :collected_at, :content_hash)"
                    ),
                    {
                        "source": "legacy-test",
                        "title": "عنوان",
                        "body": "نص",
                        "url": "https://example.test/postgres-legacy",
                        "collected_at": collected_at,
                        "content_hash": "existing-legacy-row",
                    },
                )

            with engine.begin() as connection:
                assert audit_core_schema(connection) == []
                assert stamp_existing_core_schema(connection) is True
                assert current_revisions(connection) == (BASELINE_REVISION,)

            command.upgrade(
                make_alembic_config(database_url=database_url),
                "head",
            )

            with engine.connect() as connection:
                assert audit_head_schema(connection) == []
                assert connection.scalar(text("SELECT count(*) FROM documents")) == 1
                assert connection.scalar(text("SELECT count(*) FROM raw_articles")) == 1
        finally:
            engine.dispose()


def test_postgres_adoption_detects_every_baseline_constraint_category():
    """Exercise PostgreSQL reflection, not only the SQLite fast semantics."""
    with _disposable_database() as database_url:
        engine = create_engine(database_url)
        BASELINE_METADATA.create_all(engine)

        try:
            with engine.begin() as connection:
                assert audit_core_schema(connection) == []
                inspector = inspect(connection)
                quote = connection.dialect.identifier_preparer.quote

                primary_key_name = inspector.get_pk_constraint("entity_mentions")[
                    "name"
                ]
                document_fk_name = next(
                    foreign_key["name"]
                    for foreign_key in inspector.get_foreign_keys("mentions")
                    if tuple(foreign_key["constrained_columns"])
                    == ("document_id",)
                )
                unique_name = next(
                    unique["name"]
                    for unique in inspector.get_unique_constraints(
                        "extractor_versions"
                    )
                    if tuple(unique["column_names"]) == ("name", "version")
                )

                connection.exec_driver_sql(
                    "ALTER TABLE entity_mentions DROP CONSTRAINT "
                    f"{quote(primary_key_name)}"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE mentions DROP CONSTRAINT "
                    f"{quote('ck_mention_offsets')}"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE mentions DROP CONSTRAINT "
                    f"{quote(document_fk_name)}"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE extractor_versions DROP CONSTRAINT "
                    f"{quote(unique_name)}"
                )
                connection.exec_driver_sql(
                    f"DROP INDEX {quote('ix_documents_source')}"
                )

                differences = audit_core_schema(connection)
                difference_kinds = {difference[0] for difference in differences}
                assert "primary_key_mismatch" in difference_kinds
                assert "check_constraint_mismatch" in difference_kinds
                assert "add_fk" in difference_kinds
                assert "add_constraint" in difference_kinds
                assert "add_index" in difference_kinds

                with pytest.raises(CoreSchemaMismatch, match="refusing to stamp"):
                    stamp_existing_core_schema(connection)
        finally:
            engine.dispose()


@pytest.mark.skipif(
    "pipeline_events" not in CoreBase.metadata.tables,
    reason="the append-only ledger revision is not present in this checkout",
)
def test_postgres_pipeline_events_trigger_rejects_update_and_delete():
    with _disposable_database() as database_url:
        command.upgrade(make_alembic_config(database_url=database_url), "head")
        engine = create_engine(database_url)

        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO pipeline_events "
                        "(event_key, run_id, event_type, commit_sha, "
                        "occurred_at, extractor_versions) VALUES "
                        "('trigger-test', 'run-trigger-test', 'run_started', "
                        "'abc123', CURRENT_TIMESTAMP, CAST('{}' AS jsonb))"
                    )
                )

                with pytest.raises(DBAPIError, match="append-only"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "UPDATE pipeline_events SET commit_sha = "
                                "'changed' WHERE event_key = 'trigger-test'"
                            )
                        )

                with pytest.raises(DBAPIError, match="append-only"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "DELETE FROM pipeline_events "
                                "WHERE event_key = 'trigger-test'"
                            )
                        )

                row = connection.execute(
                    text(
                        "SELECT commit_sha FROM pipeline_events "
                        "WHERE event_key = 'trigger-test'"
                    )
                ).one()
                assert row.commit_sha == "abc123"
        finally:
            engine.dispose()


@pytest.mark.skipif(
    "stable_entities" not in CoreBase.metadata.tables,
    reason="the stable-entity generation revision is not present in this checkout",
)
def test_postgres_stable_entity_history_trigger_rejects_update_and_delete():
    with _disposable_database() as database_url:
        command.upgrade(make_alembic_config(database_url=database_url), "head")
        engine = create_engine(database_url)

        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO stable_entities "
                        "(stable_uid, object_type, created_at) VALUES "
                        "('89f93f90-d068-4ee5-a9e3-8c5df8822d74', 'person', CURRENT_TIMESTAMP)"
                    )
                )

                with pytest.raises(DBAPIError, match="append-only"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "UPDATE stable_entities SET object_type = 'location' "
                                "WHERE stable_uid = '89f93f90-d068-4ee5-a9e3-8c5df8822d74'"
                            )
                        )

                with pytest.raises(DBAPIError, match="append-only"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "DELETE FROM stable_entities "
                                "WHERE stable_uid = '89f93f90-d068-4ee5-a9e3-8c5df8822d74'"
                            )
                        )

                value = connection.scalar(
                    text(
                        "SELECT object_type FROM stable_entities "
                        "WHERE stable_uid = '89f93f90-d068-4ee5-a9e3-8c5df8822d74'"
                    )
                )
                assert value == "person"
                trigger_tables = set(
                    connection.scalars(
                        text(
                            "SELECT relation.relname FROM pg_trigger trigger "
                            "JOIN pg_class relation ON relation.oid = trigger.tgrelid "
                            "WHERE trigger.tgname IN ("
                            "'trg_stable_entities_append_only', "
                            "'trg_resolver_generations_append_only', "
                            "'trg_stable_entity_snapshots_append_only', "
                            "'trg_stable_entity_memberships_append_only', "
                            "'trg_stable_entity_lineage_append_only', "
                            "'trg_stable_entity_lineage_evidence_append_only')"
                        )
                    )
                )
                assert {
                    "stable_entities",
                    "resolver_generations",
                    "stable_entity_snapshots",
                    "stable_entity_memberships",
                    "stable_entity_lineage",
                    "stable_entity_lineage_evidence",
                } <= trigger_tables
        finally:
            engine.dispose()
