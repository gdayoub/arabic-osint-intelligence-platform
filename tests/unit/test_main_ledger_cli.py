"""Focused coverage for the ledger-only operational CLI boundary."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from sqlalchemy import select

import main as cli
from src.ops.events import PipelineEventType
from src.ops.ledger import append_pipeline_event
from src.store.orm import PipelineEventORM


BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
COMMIT_SHA = "0123456789abcdef"


def test_run_core_pipeline_cli_forces_ledger_only_options(monkeypatch, capsys):
    captured: dict[str, object] = {}

    def fake_run_core_pipeline(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return SimpleNamespace(
            run_id=kwargs["run_id"],
            health=SimpleNamespace(status="healthy", stages=(), sources=()),
        )

    monkeypatch.setattr(cli, "setup_logging", lambda _level: None)
    monkeypatch.setattr(cli, "run_core_pipeline", fake_run_core_pipeline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "run-core-pipeline",
            "--run-id",
            "github-123-1",
            "--commit-sha",
            COMMIT_SHA,
            "--lease-minutes",
            "12",
            "--translation-mode",
            "best-effort",
            "--out",
            "dist/ledger-data.json",
        ],
    )

    cli.main()

    assert captured == {
        "translation_mode": "best-effort",
        "bake_out_path": Path("dist/ledger-data.json"),
        "prepare_release": False,
        "run_id": "github-123-1",
        "commit_sha": COMMIT_SHA,
        "lease_duration": timedelta(minutes=12),
    }
    assert "no release was prepared or published" in capsys.readouterr().out


def test_run_core_pipeline_parser_has_no_candidate_or_promotion_argument():
    parser = cli.build_parser()
    args = parser.parse_args(["run-core-pipeline", "--run-id", "ledger-run"])

    assert not any(
        name in vars(args)
        for name in ("release_id", "prepare_release", "promote", "publish")
    )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "run-core-pipeline",
                "--run-id",
                "ledger-run",
                "--release-id",
                "not-available",
            ]
        )


def test_reconcile_expired_pipeline_runs_appends_only_abandonment_facts(
    session, monkeypatch
):
    run_id = "expired-ledger-run"
    append_pipeline_event(
        session,
        event_key=f"{run_id}:started",
        run_id=run_id,
        event_type=PipelineEventType.RUN_STARTED,
        commit_sha=COMMIT_SHA,
        occurred_at=BASE_TIME,
        lease_expires_at=BASE_TIME + timedelta(minutes=5),
        extractor_versions={"fixture": "1.0.0"},
    )
    monkeypatch.setattr(cli, "get_core_session", lambda: nullcontext(session))

    abandoned = cli.reconcile_expired_pipeline_runs(
        now=BASE_TIME + timedelta(minutes=6),
        monitor_commit_sha="monitor-0123456789",
    )

    assert abandoned == 1
    events = list(
        session.scalars(
            select(PipelineEventORM.event_type)
            .where(PipelineEventORM.run_id == run_id)
            .order_by(PipelineEventORM.id.asc())
        )
    )
    assert events == [
        PipelineEventType.RUN_STARTED.value,
        PipelineEventType.RUN_ABANDONED.value,
    ]


def test_reconcile_pipeline_runs_cli_dispatches_the_lease_monitor(monkeypatch, capsys):
    monkeypatch.setattr(cli, "setup_logging", lambda _level: None)
    monkeypatch.setattr(cli, "reconcile_expired_pipeline_runs", lambda: 2)
    monkeypatch.setattr(sys, "argv", ["main.py", "reconcile-pipeline-runs"])

    cli.main()

    assert capsys.readouterr().out.strip() == "reconciled expired pipeline runs: abandoned=2"
