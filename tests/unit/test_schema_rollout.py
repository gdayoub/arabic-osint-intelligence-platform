"""Fast rollout-policy tests; PostgreSQL remains the production DDL gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from scripts.upgrade_core_schema import _validated_recovery_reference
from src.database.models import Base as LegacyBase
from src.store.database import init_core_db
from src.store.schema_baseline_0001 import BASELINE_METADATA
from src.store.schema_migrations import (
    BASELINE_REVISION,
    current_revisions,
    make_alembic_config,
)
from src.store.schema_rollout import (
    BASE_REVISION,
    CoreSchemaRolloutError,
    apply_core_schema_upgrade,
    plan_core_schema_upgrade,
    repository_head_revision,
    schema_upgrade_confirmation,
    verify_core_schema_at_head,
)


def _sqlite_url(tmp_path, name: str) -> str:  # noqa: ANN001
    return f"sqlite:///{tmp_path / name}"


def test_rollout_plans_exact_linear_baseline_to_head(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "plan.sqlite")
    command.upgrade(make_alembic_config(database_url=database_url), BASELINE_REVISION)
    engine = create_engine(database_url)

    try:
        with engine.connect() as connection:
            plan = plan_core_schema_upgrade(
                connection,
                expected_current=BASELINE_REVISION,
                expected_target=repository_head_revision(),
            )
        assert plan.revisions_to_apply == (
            "0002_pipeline_event_ledger",
            "0003_publication_state",
        )
    finally:
        engine.dispose()


def test_rollout_rejects_wrong_current_before_ddl(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "wrong-current.sqlite")
    command.upgrade(make_alembic_config(database_url=database_url), BASELINE_REVISION)
    engine = create_engine(database_url)

    try:
        with engine.begin() as connection:
            with pytest.raises(CoreSchemaRolloutError, match="expected exactly base"):
                apply_core_schema_upgrade(
                    connection,
                    expected_current=BASE_REVISION,
                    expected_target=repository_head_revision(),
                    confirmation=schema_upgrade_confirmation(
                        BASE_REVISION,
                        repository_head_revision(),
                    ),
                )
            assert current_revisions(connection) == (BASELINE_REVISION,)
            assert "pipeline_events" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_rollout_requires_exact_confirmation_before_ddl(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "confirmation.sqlite")
    command.upgrade(make_alembic_config(database_url=database_url), BASELINE_REVISION)
    engine = create_engine(database_url)

    try:
        with engine.begin() as connection:
            with pytest.raises(CoreSchemaRolloutError, match="did not match exactly"):
                apply_core_schema_upgrade(
                    connection,
                    expected_current=BASELINE_REVISION,
                    expected_target=repository_head_revision(),
                    confirmation="upgrade it",
                )
            assert current_revisions(connection) == (BASELINE_REVISION,)
    finally:
        engine.dispose()


def test_rollout_preserves_frozen_legacy_tables_and_rows(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "legacy.sqlite")
    command.upgrade(make_alembic_config(database_url=database_url), BASELINE_REVISION)
    engine = create_engine(database_url)
    LegacyBase.metadata.create_all(engine)
    collected_at = datetime(2026, 8, 20, tzinfo=timezone.utc)

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO raw_articles "
                    "(source, title, body, url, collected_at, content_hash) "
                    "VALUES ('legacy', 'عنوان', 'نص', "
                    "'https://example.test/legacy', :collected_at, 'raw-hash')"
                ),
                {"collected_at": collected_at},
            )
            connection.execute(
                text(
                    "INSERT INTO processed_articles "
                    "(raw_article_id, cleaned_text, topic, "
                    "sentiment_or_escalation, processed_at) "
                    "VALUES (1, 'نص', 'other', 'neutral', :processed_at)"
                ),
                {"processed_at": collected_at},
            )

        target = repository_head_revision()
        with engine.begin() as connection:
            apply_core_schema_upgrade(
                connection,
                expected_current=BASELINE_REVISION,
                expected_target=target,
                confirmation=schema_upgrade_confirmation(BASELINE_REVISION, target),
            )

        with engine.connect() as connection:
            assert verify_core_schema_at_head(connection) == target
            assert connection.scalar(text("SELECT count(*) FROM raw_articles")) == 1
            assert (
                connection.scalar(text("SELECT count(*) FROM processed_articles"))
                == 1
            )
    finally:
        engine.dispose()


def test_init_core_db_uses_alembic_for_an_empty_database(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "empty-init.sqlite")
    init_core_db(database_url)
    engine = create_engine(database_url)

    try:
        with engine.connect() as connection:
            assert verify_core_schema_at_head(connection) == repository_head_revision()
            assert current_revisions(connection) == (repository_head_revision(),)
    finally:
        engine.dispose()


def test_init_core_db_refuses_unversioned_create_all_schema(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "unversioned.sqlite")
    engine = create_engine(database_url)
    BASELINE_METADATA.create_all(engine)

    try:
        with pytest.raises(CoreSchemaRolloutError, match="unversioned"):
            init_core_db(database_url)
        with engine.connect() as connection:
            assert current_revisions(connection) == ()
            assert "pipeline_events" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_apply_requires_a_recorded_recovery_reference():
    with pytest.raises(CoreSchemaRolloutError, match="restore point"):
        _validated_recovery_reference("  ")
    assert _validated_recovery_reference("neon-branch-before-0003") == (
        "neon-branch-before-0003"
    )


def test_every_core_writer_workflow_verifies_head_and_shares_the_lock():
    repository_root = Path(__file__).resolve().parents[2]
    writer_workflows = (
        "pipeline.yml",
        "maintenance.yml",
        "review-decision.yml",
    )

    for workflow_name in writer_workflows:
        workflow = (
            repository_root / ".github" / "workflows" / workflow_name
        ).read_text(encoding="utf-8")
        assert "group: osint-neon-writer" in workflow
        assert "python scripts/verify_core_schema.py" in workflow
        assert "init-core-db" not in workflow


def test_schema_upgrade_workflow_keeps_confirmation_and_postgres_preflight():
    repository_root = Path(__file__).resolve().parents[2]
    workflow = (
        repository_root / ".github" / "workflows" / "schema-upgrade.yml"
    ).read_text(encoding="utf-8")

    assert "group: osint-neon-writer" in workflow
    assert "postgres:16-alpine" in workflow
    assert "tests/integration/test_migrations_postgres.py" in workflow
    assert '--confirmation "$CONFIRMATION"' in workflow
    assert '--recovery-reference "$RECOVERY_REFERENCE"' in workflow
    assert "python scripts/verify_core_schema.py" in workflow
