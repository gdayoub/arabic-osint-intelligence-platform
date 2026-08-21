"""CLI-level checks for the resumable M4.2a identity adoption."""

from __future__ import annotations

import json
import sys

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from scripts import adopt_m42_identity
from src.store.identity import apply_identity_adoption, legacy_document_uid
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
