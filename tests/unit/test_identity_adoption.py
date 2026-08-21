"""CLI-level checks for the resumable M4.2a identity adoption."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from scripts import adopt_m42_identity
import src.store.identity as identity
from src.store.identity import (
    IdentityInvariantError,
    apply_identity_adoption,
    legacy_document_uid,
    plan_identity_adoption,
)
from src.store.orm import (
    DocumentIdentityORM,
    DocumentORM,
    EvidenceIdentityORM,
    ExtractorVersionORM,
    MentionEvidenceIdentityORM,
    MentionORM,
    ResolutionConstraintORM,
    ResolutionDecisionORM,
)
from src.store.schema_migrations import make_alembic_config
from src.store.provenance import create_document, register_extractor_version


def _sqlite_url(tmp_path) -> str:  # noqa: ANN001
    return f"sqlite:///{tmp_path / 'identity-adoption.sqlite'}"


def _seed_legacy_rows(engine) -> tuple[int, int]:  # noqa: ANN001
    """Insert old-style rows directly, before the new sanctioned dual write."""

    text = "ذكر محمد أحمد في الخبر"
    mention_text = "محمد أحمد"
    with Session(engine) as session:
        extractor = ExtractorVersionORM(name="legacy_gazetteer", version="0.9.0")
        session.add(extractor)
        session.flush()
        documents = [
            DocumentORM(source="legacy", text=text, content_hash="legacy-one"),
            DocumentORM(source="legacy", text=text, content_hash="legacy-two"),
        ]
        session.add_all(documents)
        session.flush()
        start = text.index(mention_text)
        mentions = [
            MentionORM(
                document_id=document.id,
                text=mention_text,
                start_offset=start,
                end_offset=start + len(mention_text),
                object_type="person",
                extractor_version_id=extractor.id,
            )
            for document in documents
        ]
        session.add_all(mentions)
        session.flush()
        decision = ResolutionDecisionORM(
            left_mention_id=mentions[0].id,
            right_mention_id=mentions[1].id,
            decision="same",
            source="legacy-review",
            reviewer="legacy-analyst",
            extractor_version_id=extractor.id,
        )
        session.add(decision)
        session.commit()
        return documents[0].id, documents[1].id


def _run_cli(monkeypatch, capsys, args: list[str]):  # noqa: ANN001
    monkeypatch.setattr(sys, "argv", ["adopt_m42_identity.py", *args])
    exit_code = adopt_m42_identity.main()
    stdout = capsys.readouterr().out
    return exit_code, json.loads(stdout)


def test_check_apply_and_resume_are_json_and_idempotent(
    tmp_path, monkeypatch, capsys, blob_store
):
    database_url = _sqlite_url(tmp_path)
    command.upgrade(make_alembic_config(database_url=database_url), "head")
    engine = create_engine(database_url)
    first_document_id, second_document_id = _seed_legacy_rows(engine)
    monkeypatch.setattr(adopt_m42_identity, "get_blob_store", lambda: blob_store)

    try:
        missing_language_code, missing_language = _run_cli(
            monkeypatch,
            capsys,
            ["--check", "--database-url", database_url],
        )
        assert missing_language_code == 1
        assert missing_language["ready"] is False
        assert {issue["kind"] for issue in missing_language["errors"]} == {
            "missing_language",
            "decision_missing_evidence",
        }

        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(DocumentIdentityORM)) == 0
            assert session.scalar(select(func.count()).select_from(MentionEvidenceIdentityORM)) == 0

        check_code, check = _run_cli(
            monkeypatch,
            capsys,
            [
                "--check",
                "--database-url",
                database_url,
                "--extractor-language",
                "legacy_gazetteer=ar",
            ],
        )
        assert check_code == 0
        assert check["mode"] == "check"
        assert check["ready"] is True
        assert check["documents"]["identities_missing"] == 2
        assert check["mentions"]["mappings_missing"] == 2
        assert check["decisions"]["constraints_missing"] == 1
        assert check["applied"] is None

        apply_args = [
            "--apply",
            "--database-url",
            database_url,
            "--extractor-language",
            "legacy_gazetteer=ar",
        ]
        apply_code, applied = _run_cli(monkeypatch, capsys, apply_args)
        assert apply_code == 0
        assert applied["mode"] == "apply"
        assert applied["ready"] is True
        assert applied["applied"] == {
            "document_identities": 2,
            "mention_mappings": 2,
            "resolution_constraints": 1,
        }
        assert applied["remap_status_counts"] == {"remapped": 1}

        with Session(engine) as session:
            document_identities = list(
                session.scalars(
                    select(DocumentIdentityORM).order_by(DocumentIdentityORM.document_id)
                )
            )
            assert [row.document_uid for row in document_identities] == [
                legacy_document_uid(first_document_id),
                legacy_document_uid(second_document_id),
            ]
            assert session.scalar(select(func.count()).select_from(EvidenceIdentityORM)) == 2
            assert session.scalar(select(func.count()).select_from(MentionEvidenceIdentityORM)) == 2
            assert session.scalar(select(func.count()).select_from(ResolutionConstraintORM)) == 1

        rerun_code, rerun = _run_cli(monkeypatch, capsys, apply_args)
        assert rerun_code == 0
        assert rerun["ready"] is True
        assert rerun["applied"] == {
            "document_identities": 0,
            "mention_mappings": 0,
            "resolution_constraints": 0,
        }
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(DocumentIdentityORM)) == 2
            assert session.scalar(select(func.count()).select_from(MentionEvidenceIdentityORM)) == 2
            assert session.scalar(select(func.count()).select_from(ResolutionConstraintORM)) == 1
    finally:
        engine.dispose()


def test_adoption_keeps_a_preexisting_new_document_uid_when_only_mapping_is_missing(
    session, blob_store
):
    text = "ذكر محمد أحمد في الخبر"
    document = create_document(
        session,
        source="mixed-era",
        text=text,
        content_hash="mixed-era-document",
        blob_store=blob_store,
    )
    original_uid = session.scalar(
        select(DocumentIdentityORM.document_uid).where(
            DocumentIdentityORM.document_id == document.id
        )
    )
    extractor = register_extractor_version(session, "legacy_gazetteer", "0.9.0")
    mention_text = "محمد أحمد"
    start = text.index(mention_text)
    raw_legacy_mention = MentionORM(
        document_id=document.id,
        text=mention_text,
        start_offset=start,
        end_offset=start + len(mention_text),
        object_type="person",
        extractor_version_id=extractor.id,
    )
    session.add(raw_legacy_mention)
    session.flush()

    report = apply_identity_adoption(
        session,
        blob_store=blob_store,
        extractor_languages={"legacy_gazetteer": "ar"},
    )

    assert report.ready is True
    assert report.applied == {
        "document_identities": 0,
        "mention_mappings": 1,
        "resolution_constraints": 0,
    }
    assert session.scalar(
        select(DocumentIdentityORM.document_uid).where(
            DocumentIdentityORM.document_id == document.id
        )
    ) == original_uid
    assert session.get(MentionEvidenceIdentityORM, raw_legacy_mention.id) is not None


def test_apply_batches_a_legacy_constraint_supersession_in_append_order(
    session, blob_store
):
    """A child constraint waits only for its new predecessor's ID barrier."""

    first_document_id, second_document_id = _seed_legacy_rows(session.get_bind())
    with Session(session.get_bind()) as legacy_session:
        extractor = legacy_session.scalar(
            select(ExtractorVersionORM).where(
                ExtractorVersionORM.name == "legacy_gazetteer"
            )
        )
        first_mention = legacy_session.scalar(
            select(MentionORM).where(MentionORM.document_id == first_document_id)
        )
        second_mention = legacy_session.scalar(
            select(MentionORM).where(MentionORM.document_id == second_document_id)
        )
        first_decision = legacy_session.scalar(
            select(ResolutionDecisionORM).order_by(ResolutionDecisionORM.id)
        )
        replacement = ResolutionDecisionORM(
            left_mention_id=first_mention.id,
            right_mention_id=second_mention.id,
            decision="different",
            source="legacy-review",
            reviewer="legacy-analyst",
            extractor_version_id=extractor.id,
            supersedes_id=first_decision.id,
        )
        legacy_session.add(replacement)
        legacy_session.commit()

    with session.begin():
        report = apply_identity_adoption(
            session,
            blob_store=blob_store,
            extractor_languages={"legacy_gazetteer": "ar"},
        )

    assert report.ready is True
    constraints = list(
        session.scalars(
            select(ResolutionConstraintORM).order_by(ResolutionConstraintORM.source_decision_id)
        )
    )
    assert len(constraints) == 2
    assert constraints[1].supersedes_constraint_id == constraints[0].id


def test_apply_batches_large_legacy_writes_and_keeps_the_final_plan_check(
    session, blob_store, monkeypatch
):
    """Large adoptions use bounded fetches, not one database round trip per row.

    The production recovery has thousands of mappings.  This deliberately
    creates more than a thousand distinct legacy mentions, then observes the
    whole apply call (including its required final relational re-check).
    A per-mention ``session.get``/``flush`` loop would issue thousands of
    SELECTs here; the batched writer stays below a small fixed ceiling.
    """

    extractor = ExtractorVersionORM(name="legacy_bulk", version="0.9.0")
    session.add(extractor)
    session.flush()

    document_ids: list[int] = []
    mentions: list[MentionORM] = []
    for document_number in range(12):
        mention_texts = [
            f"legacy-{document_number:02d}-{mention_number:03d}"
            for mention_number in range(100)
        ]
        text = " ".join(mention_texts)
        document = DocumentORM(
            source="legacy-bulk",
            text=text,
            content_hash=f"legacy-bulk-{document_number}",
        )
        session.add(document)
        session.flush()
        document_ids.append(document.id)
        for mention_text in mention_texts:
            start = text.index(mention_text)
            mentions.append(
                MentionORM(
                    document_id=document.id,
                    text=mention_text,
                    start_offset=start,
                    end_offset=start + len(mention_text),
                    object_type="person",
                    extractor_version_id=extractor.id,
                )
            )
    session.add_all(mentions)
    session.commit()

    source_reads: list[int] = []
    original_resolve = identity.resolve_document_text

    def count_source_reads(document, store):  # noqa: ANN001
        source_reads.append(document.id)
        return original_resolve(document, store)

    monkeypatch.setattr(identity, "resolve_document_text", count_source_reads)

    statements: list[str] = []
    select_statements: list[str] = []
    select_bind_counts: list[int] = []
    engine = session.get_bind()

    def count_selects(  # noqa: ANN001
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        statements.append(statement)
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)
            if isinstance(_parameters, (list, tuple)):
                select_bind_counts.append(len(_parameters))

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with session.begin():
            report = apply_identity_adoption(
                session,
                blob_store=blob_store,
                extractor_languages={"legacy_bulk": "en"},
            )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert report.ready is True
    assert report.applied == {
        "document_identities": 12,
        "mention_mappings": 1200,
        "resolution_constraints": 0,
    }
    # The existing final planner only source-validates unmapped mentions.  By
    # then this successful apply has mapped all rows, so it performs its full
    # relational snapshot/reconciliation without a second needless blob read.
    assert source_reads == document_ids
    # Both planner snapshots and the final reconciliation query their complete
    # relational state.  The fixed ceilings leave room for those checks and
    # the three 500-row INSERT pages per high-cardinality table, while making
    # an accidental per-mention writer query/insert (1,200+) impossible to
    # hide.  The SELECT pages also remain compatible with conservative SQLite
    # bind-variable limits rather than assuming the local build's larger cap.
    assert len(select_statements) < 60
    assert len(statements) < 70
    assert max(select_bind_counts) <= 500

    assert session.scalar(select(func.count()).select_from(DocumentIdentityORM)) == 12
    assert session.scalar(select(func.count()).select_from(EvidenceIdentityORM)) == 1200
    assert session.scalar(select(func.count()).select_from(MentionEvidenceIdentityORM)) == 1200


def test_apply_rolls_back_batched_rows_when_a_late_mapping_write_fails(session, blob_store):
    """The caller's one transaction rolls back identities and evidence together."""

    first_document_id, second_document_id = _seed_legacy_rows(session.get_bind())
    session.commit()
    engine = session.get_bind()

    def fail_mapping_insert(  # noqa: ANN001
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if "INSERT INTO mention_evidence_identities" in statement:
            raise RuntimeError("forced mapping failure")

    event.listen(engine, "before_cursor_execute", fail_mapping_insert)
    try:
        with pytest.raises(RuntimeError, match="forced mapping failure"):
            with session.begin():
                apply_identity_adoption(
                    session,
                    blob_store=blob_store,
                    extractor_languages={"legacy_gazetteer": "ar"},
                )
    finally:
        event.remove(engine, "before_cursor_execute", fail_mapping_insert)

    # The failure happens after document/evidence rows have already crossed
    # their ID-assignment flush barriers.  A single surrounding transaction is
    # therefore essential: it must leave every durable table untouched.
    assert first_document_id > 0
    assert second_document_id > 0
    assert session.scalar(select(func.count()).select_from(DocumentIdentityORM)) == 0
    assert session.scalar(select(func.count()).select_from(EvidenceIdentityORM)) == 0
    assert session.scalar(select(func.count()).select_from(MentionEvidenceIdentityORM)) == 0
    assert session.scalar(select(func.count()).select_from(ResolutionConstraintORM)) == 0


def test_read_only_plan_resolves_each_document_once_and_keeps_unsafe_span_rows(
    session, blob_store, monkeypatch
):
    """A detached audit must not turn repeated mentions into repeated R2 reads."""

    extractor = ExtractorVersionORM(name="legacy_gazetteer", version="0.9.0")
    first_document = DocumentORM(
        source="legacy",
        text="alpha beta",
        content_hash="cache-first",
    )
    second_document = DocumentORM(
        source="legacy",
        text="gamma",
        content_hash="cache-second",
    )
    session.add_all([extractor, first_document, second_document])
    session.flush()
    mentions = [
        # First document, bad text at an otherwise valid span.
        MentionORM(
            document_id=first_document.id,
            text="wrong",
            start_offset=0,
            end_offset=5,
            object_type="person",
            extractor_version_id=extractor.id,
        ),
        # A valid mention from another document deliberately sits between the
        # two first-document mentions, proving grouping is not relying on row
        # adjacency.
        MentionORM(
            document_id=second_document.id,
            text="gamma",
            start_offset=0,
            end_offset=5,
            object_type="person",
            extractor_version_id=extractor.id,
        ),
        # Same first document, unsafe offsets.
        MentionORM(
            document_id=first_document.id,
            text="x",
            start_offset=20,
            end_offset=21,
            object_type="person",
            extractor_version_id=extractor.id,
        ),
    ]
    session.add_all(mentions)
    session.flush()
    first_document_id = first_document.id
    second_document_id = second_document.id
    first_mention_id, _, third_mention_id = (mention.id for mention in mentions)
    session.commit()

    calls: list[int] = []
    original_resolve = identity.resolve_document_text

    def count_resolutions(document, store):  # noqa: ANN001
        # release_database_connection=True must leave the slow blob phase
        # without an open SQLAlchemy transaction.
        assert session.in_transaction() is False
        calls.append(document.id)
        return original_resolve(document, store)

    monkeypatch.setattr(identity, "resolve_document_text", count_resolutions)
    report = plan_identity_adoption(
        session,
        blob_store=blob_store,
        extractor_languages={"legacy_gazetteer": "ar"},
        release_database_connection=True,
    )

    assert calls == [first_document_id, second_document_id]
    assert report.ready is False
    assert [issue.as_dict() for issue in report.errors] == [
        {
            "kind": "invalid_source_span",
            "row_id": first_mention_id,
            "detail": "mention text does not match original source offsets",
        },
        {
            "kind": "invalid_source_span",
            "row_id": third_mention_id,
            "detail": "mention offsets are outside original source text",
        },
    ]
    # A read-only plan stays fail-closed: even its successfully validated
    # second-document mention cannot create a partial mapping next to errors.
    assert session.scalar(select(func.count()).select_from(DocumentIdentityORM)) == 0
    assert session.scalar(select(func.count()).select_from(MentionEvidenceIdentityORM)) == 0


def test_detached_read_only_plan_refuses_to_rollback_a_callers_transaction(session, blob_store):
    # A dedicated CLI session starts clean.  A library caller might instead
    # have work in progress, which a connection-release rollback must never
    # discard merely to speed an audit.
    session.add(DocumentORM(source="pending", text="text", content_hash="pending"))

    with pytest.raises(IdentityInvariantError, match="caller-owned transaction"):
        plan_identity_adoption(
            session,
            blob_store=blob_store,
            release_database_connection=True,
        )

    assert len(session.new) == 1


def test_manual_adoption_workflow_checks_before_any_confirmed_apply():
    repository_root = Path(__file__).resolve().parents[2]
    workflow = (
        repository_root / ".github" / "workflows" / "evidence-identity-adoption.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "- check" in workflow
    assert "- apply" in workflow
    assert "group: osint-neon-writer" in workflow
    assert "python scripts/verify_core_schema.py" in workflow
    assert "python scripts/adopt_m42_identity.py --check" in workflow
    assert "python scripts/adopt_m42_identity.py --apply" in workflow
    assert workflow.index("adopt_m42_identity.py --check") < workflow.index(
        "adopt_m42_identity.py --apply"
    )
    assert "ADOPT M4.2 EVIDENCE IDENTITY" in workflow
    assert "BLOB_BACKEND: r2" in workflow
    assert "cloudflare/wrangler-action" not in workflow
