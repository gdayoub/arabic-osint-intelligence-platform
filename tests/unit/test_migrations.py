"""Fast migration semantics; PostgreSQL 16 remains the promotion gate."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    MetaData,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)

from src.database.models import Base as LegacyBase
from src.store.orm import CoreBase
from src.store.schema_baseline_0001 import BASELINE_METADATA, BASELINE_TABLE_NAMES
from src.store.schema_migrations import (
    BASELINE_REVISION,
    CORE_TABLE_NAMES,
    CoreSchemaMismatch,
    audit_core_schema,
    audit_head_schema,
    current_revisions,
    make_alembic_config,
    stamp_existing_core_schema,
)


def _sqlite_url(tmp_path, name: str) -> str:  # noqa: ANN001
    return f"sqlite:///{tmp_path / name}"


def _clone_baseline_metadata() -> MetaData:
    metadata = MetaData()
    for table in BASELINE_METADATA.sorted_tables:
        table.to_metadata(metadata)
    return metadata


def _assert_stamp_refused(engine, difference_kind: str) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        differences = audit_core_schema(connection)
        assert any(difference[0] == difference_kind for difference in differences)

        with pytest.raises(CoreSchemaMismatch, match="refusing to stamp"):
            stamp_existing_core_schema(connection)

        assert "alembic_version" not in inspect(connection).get_table_names()


def test_empty_database_upgrades_to_head_with_exact_baseline(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "empty-to-head.sqlite")
    config = make_alembic_config(database_url=database_url)
    expected_head = ScriptDirectory.from_config(config).get_current_head()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert current_revisions(connection) == (expected_head,)
            assert audit_head_schema(connection) == []
            table_names = set(inspect(connection).get_table_names())
            assert set(CORE_TABLE_NAMES) | {"alembic_version"} <= table_names
    finally:
        engine.dispose()


def test_0003_upgrades_to_0004_evidence_identity_without_rebuilding_core_tables(
    tmp_path,  # noqa: ANN001
):
    database_url = _sqlite_url(tmp_path, "0003-to-0004.sqlite")
    config = make_alembic_config(database_url=database_url)
    command.upgrade(config, "0003_publication_state")
    engine = create_engine(database_url)

    try:
        with engine.connect() as connection:
            assert current_revisions(connection) == ("0003_publication_state",)
            before = set(inspect(connection).get_table_names())
            assert "document_identities" not in before
            assert "evidence_identities" not in before

        command.upgrade(config, "0004_evidence_identity")

        with engine.connect() as connection:
            assert current_revisions(connection) == ("0004_evidence_identity",)
            after = set(inspect(connection).get_table_names())
            assert {
                "document_identities",
                "evidence_identities",
                "mention_evidence_identities",
                "resolution_constraints",
            } <= after
            assert "stable_entities" not in after
    finally:
        engine.dispose()


def test_0004_upgrades_to_0005_stable_entity_generations_without_rebuilding_identity_tables(
    tmp_path,  # noqa: ANN001
):
    database_url = _sqlite_url(tmp_path, "0004-to-0005.sqlite")
    config = make_alembic_config(database_url=database_url)
    command.upgrade(config, "0004_evidence_identity")
    engine = create_engine(database_url)

    try:
        with engine.connect() as connection:
            assert current_revisions(connection) == ("0004_evidence_identity",)
            before = set(inspect(connection).get_table_names())
            assert "stable_entities" not in before
            assert "evidence_identities" in before

        command.upgrade(config, "head")

        with engine.connect() as connection:
            assert current_revisions(connection) == ("0005_stable_entity_generations",)
            after = set(inspect(connection).get_table_names())
            assert {
                "stable_entities",
                "resolver_generations",
                "stable_entity_resolution_state",
                "stable_entity_snapshots",
                "stable_entity_memberships",
                "stable_entity_lineage",
                "stable_entity_lineage_evidence",
            } <= after
            assert "evidence_identities" in after
            assert audit_head_schema(connection) == []
    finally:
        engine.dispose()


def test_empty_database_upgrades_to_exact_adoption_baseline(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "empty-to-baseline.sqlite")
    config = make_alembic_config(database_url=database_url)

    command.upgrade(config, BASELINE_REVISION)

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


def test_existing_core_schema_is_audited_and_stamped_without_touching_data(
    tmp_path,  # noqa: ANN001
):
    database_url = _sqlite_url(tmp_path, "existing-schema.sqlite")
    engine = create_engine(database_url)
    LegacyBase.metadata.create_all(engine)
    BASELINE_METADATA.create_all(engine)

    collected_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents "
                "(source, content_hash, collected_at, retracted) "
                "VALUES (:source, :content_hash, :collected_at, :retracted)"
            ),
            {
                "source": "adoption-test",
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
                "url": "https://example.test/legacy",
                "collected_at": collected_at,
                "content_hash": "existing-legacy-row",
            },
        )

    try:
        with engine.begin() as connection:
            assert audit_core_schema(connection) == []
            assert current_revisions(connection) == ()
            assert stamp_existing_core_schema(connection) is True
            assert stamp_existing_core_schema(connection) is False

            assert connection.scalar(text("SELECT count(*) FROM documents")) == 1
            assert connection.scalar(text("SELECT count(*) FROM raw_articles")) == 1
            assert {"raw_articles", "processed_articles"}.issubset(
                set(inspect(connection).get_table_names())
            )
    finally:
        engine.dispose()


def test_adoption_refuses_core_schema_drift(tmp_path):  # noqa: ANN001
    database_url = _sqlite_url(tmp_path, "drifted-schema.sqlite")
    engine = create_engine(database_url)
    CoreBase.metadata.create_all(engine)

    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE documents ADD COLUMN unexpected TEXT"))

            with pytest.raises(CoreSchemaMismatch, match="refusing to stamp"):
                stamp_existing_core_schema(connection)

            assert "alembic_version" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_adoption_target_does_not_grow_with_live_corebase(tmp_path):  # noqa: ANN001
    # This assertion remains valid in the foundation-only commit (where the
    # sets are equal) and after later revisions expand live CoreBase.
    assert BASELINE_METADATA is not CoreBase.metadata
    assert BASELINE_TABLE_NAMES <= frozenset(CoreBase.metadata.tables)

    database_url = _sqlite_url(tmp_path, "frozen-baseline.sqlite")
    engine = create_engine(database_url)
    BASELINE_METADATA.create_all(engine)
    try:
        with engine.connect() as connection:
            assert audit_core_schema(connection) == []
    finally:
        engine.dispose()


def test_adoption_refuses_unversioned_post_baseline_managed_table(tmp_path):  # noqa: ANN001
    post_baseline_tables = sorted(
        set(CoreBase.metadata.tables).difference(BASELINE_TABLE_NAMES)
    )
    if not post_baseline_tables:
        pytest.skip("no post-baseline managed table exists in this revision")

    database_url = _sqlite_url(tmp_path, "premature-managed-table.sqlite")
    engine = create_engine(database_url)
    BASELINE_METADATA.create_all(engine)
    CoreBase.metadata.tables[post_baseline_tables[0]].create(engine)

    try:
        _assert_stamp_refused(engine, "post_baseline_managed_tables_present")
    finally:
        engine.dispose()


def test_adoption_refuses_missing_check_constraint(tmp_path):  # noqa: ANN001
    metadata = _clone_baseline_metadata()
    mentions = metadata.tables["mentions"]
    check = next(
        constraint
        for constraint in mentions.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_mention_offsets"
    )
    mentions.constraints.remove(check)

    engine = create_engine(_sqlite_url(tmp_path, "missing-check.sqlite"))
    metadata.create_all(engine)
    try:
        _assert_stamp_refused(engine, "check_constraint_mismatch")
    finally:
        engine.dispose()


def test_adoption_refuses_changed_same_name_check_expression(tmp_path):  # noqa: ANN001
    metadata = _clone_baseline_metadata()
    mentions = metadata.tables["mentions"]
    check = next(
        constraint
        for constraint in mentions.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_mention_offsets"
    )
    mentions.constraints.remove(check)
    mentions.append_constraint(
        CheckConstraint(
            "end_offset >= start_offset",
            name="ck_mention_offsets",
        )
    )

    engine = create_engine(_sqlite_url(tmp_path, "changed-check.sqlite"))
    metadata.create_all(engine)
    try:
        _assert_stamp_refused(engine, "check_constraint_mismatch")
    finally:
        engine.dispose()


def test_adoption_refuses_missing_primary_key(tmp_path):  # noqa: ANN001
    engine = create_engine(_sqlite_url(tmp_path, "missing-primary-key.sqlite"))
    BASELINE_METADATA.create_all(engine)
    try:
        with engine.begin() as connection:
            # entity_mentions has no dependants, so this rebuild isolates the
            # primary-key drift without changing any other baseline object.
            connection.execute(text("DROP TABLE entity_mentions"))
            connection.execute(
                text(
                    "CREATE TABLE entity_mentions ("
                    "entity_id INTEGER NOT NULL REFERENCES entities (id), "
                    "mention_id INTEGER NOT NULL REFERENCES mentions (id)"
                    ")"
                )
            )
        _assert_stamp_refused(engine, "primary_key_mismatch")
    finally:
        engine.dispose()


def test_alembic_layer_refuses_missing_index(tmp_path):  # noqa: ANN001
    metadata = _clone_baseline_metadata()
    documents = metadata.tables["documents"]
    source_index = next(
        index for index in documents.indexes if index.name == "ix_documents_source"
    )
    documents.indexes.remove(source_index)

    engine = create_engine(_sqlite_url(tmp_path, "missing-index.sqlite"))
    metadata.create_all(engine)
    try:
        _assert_stamp_refused(engine, "add_index")
    finally:
        engine.dispose()


def test_alembic_layer_refuses_missing_unique_constraint(tmp_path):  # noqa: ANN001
    metadata = _clone_baseline_metadata()
    extractor_versions = metadata.tables["extractor_versions"]
    unique = next(
        constraint
        for constraint in extractor_versions.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    extractor_versions.constraints.remove(unique)

    engine = create_engine(_sqlite_url(tmp_path, "missing-unique.sqlite"))
    metadata.create_all(engine)
    try:
        _assert_stamp_refused(engine, "add_constraint")
    finally:
        engine.dispose()


def test_alembic_layer_refuses_missing_foreign_key(tmp_path):  # noqa: ANN001
    metadata = _clone_baseline_metadata()
    mentions = metadata.tables["mentions"]
    document_foreign_key = next(
        constraint
        for constraint in mentions.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and tuple(constraint.column_keys) == ("document_id",)
    )
    mentions.constraints.remove(document_foreign_key)

    engine = create_engine(_sqlite_url(tmp_path, "missing-foreign-key.sqlite"))
    metadata.create_all(engine)
    try:
        _assert_stamp_refused(engine, "add_fk")
    finally:
        engine.dispose()


def test_postgresql_membership_reflection_normalizes_to_baseline_expression():
    from src.store.schema_migrations import _normalize_check_expression

    baseline = "decision IN ('same', 'different')"
    reflected = (
        "((decision)::text = ANY "
        "((ARRAY['same'::character varying, "
        "'different'::character varying])::text[]))"
    )

    assert _normalize_check_expression(reflected) == _normalize_check_expression(
        baseline
    )
