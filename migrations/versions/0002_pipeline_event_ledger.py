"""Add the append-only operational pipeline event ledger.

Revision ID: 0002_pipeline_event_ledger
Revises: 0001_core_baseline
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_pipeline_event_ledger"
down_revision: str | None = "0001_core_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EVENT_TYPES = (
    "run_started",
    "run_heartbeat",
    "run_succeeded",
    "run_failed",
    "run_abandoned",
    "stage_started",
    "stage_succeeded",
    "stage_failed",
    "source_started",
    "source_succeeded",
    "source_failed",
    "release_reserved",
    "release_candidate_created",
    "promotion_started",
    "release_published",
    "release_failed",
    "release_superseded",
)

_REASON_CODES = (
    "unexpected_error",
    "upstream_stage_failed",
    "source_fetch_failed",
    "source_selector_failed",
    "source_parse_failed",
    "source_zero_yield",
    "data_stale",
    "count_invariant_failed",
    "lease_expired",
    "release_contract_failed",
    "release_publish_failed",
)


def _quoted_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _portable_json() -> sa.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    event_id_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "pipeline_events",
        sa.Column("id", event_id_type, autoincrement=True, nullable=False),
        sa.Column("event_key", sa.String(length=180), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("release_id", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("input_count", sa.Integer(), nullable=True),
        sa.Column("output_count", sa.Integer(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.Column("inserted_count", sa.Integer(), nullable=True),
        sa.Column("selector_failure_count", sa.Integer(), nullable=True),
        sa.Column("parsing_failure_count", sa.Integer(), nullable=True),
        sa.Column("latest_successful_article_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extractor_versions", _portable_json(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("data_sequence", event_id_type, nullable=True),
        sa.Column("promotion_sequence", event_id_type, nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("rollback_of_promotion_sequence", event_id_type, nullable=True),
        sa.CheckConstraint(
            f"event_type IN ({_quoted_values(_EVENT_TYPES)})",
            name="ck_pipeline_event_type",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR "
            f"reason_code IN ({_quoted_values(_REASON_CODES)})",
            name="ck_pipeline_event_reason_code",
        ),
        sa.CheckConstraint(
            "input_count IS NULL OR input_count >= 0",
            name="ck_pipeline_event_input_count",
        ),
        sa.CheckConstraint(
            "output_count IS NULL OR output_count >= 0",
            name="ck_pipeline_event_output_count",
        ),
        sa.CheckConstraint(
            "error_count IS NULL OR error_count >= 0",
            name="ck_pipeline_event_error_count",
        ),
        sa.CheckConstraint(
            "attempt_count IS NULL OR attempt_count >= 0",
            name="ck_pipeline_event_attempt_count",
        ),
        sa.CheckConstraint(
            "inserted_count IS NULL OR inserted_count >= 0",
            name="ck_pipeline_event_inserted_count",
        ),
        sa.CheckConstraint(
            "selector_failure_count IS NULL OR selector_failure_count >= 0",
            name="ck_pipeline_event_selector_failure_count",
        ),
        sa.CheckConstraint(
            "parsing_failure_count IS NULL OR parsing_failure_count >= 0",
            name="ck_pipeline_event_parsing_failure_count",
        ),
        sa.CheckConstraint(
            "data_sequence IS NULL OR data_sequence > 0",
            name="ck_pipeline_event_data_sequence",
        ),
        sa.CheckConstraint(
            "promotion_sequence IS NULL OR promotion_sequence > 0",
            name="ck_pipeline_event_promotion_sequence",
        ),
        sa.CheckConstraint(
            "rollback_of_promotion_sequence IS NULL "
            "OR rollback_of_promotion_sequence > 0",
            name="ck_pipeline_event_rollback_promotion_sequence",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_pipeline_events_event_key"),
    )
    op.create_index("ix_pipeline_events_occurred_at", "pipeline_events", ["occurred_at"])
    op.create_index("ix_pipeline_events_release_id", "pipeline_events", ["release_id"])
    op.create_index("ix_pipeline_events_run_id", "pipeline_events", ["run_id"])
    op.create_index(
        "ix_pipeline_events_data_sequence", "pipeline_events", ["data_sequence"]
    )
    op.create_index(
        "ix_pipeline_events_promotion_sequence",
        "pipeline_events",
        ["promotion_sequence"],
    )
    op.create_index(
        "ix_pipeline_events_run_time",
        "pipeline_events",
        ["run_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_pipeline_events_stage",
        "pipeline_events",
        ["stage", "occurred_at"],
    )
    op.create_index(
        "ix_pipeline_events_source",
        "pipeline_events",
        ["source", "occurred_at"],
    )

    # ORM hooks protect normal local/SQLite use.  PostgreSQL gets the real
    # database boundary so raw SQL and a different application process also
    # cannot rewrite operational history.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_pipeline_event_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                RAISE EXCEPTION 'pipeline_events is append-only'
                    USING ERRCODE = '55000';
            END;
            $function$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_pipeline_events_append_only
            BEFORE UPDATE OR DELETE ON pipeline_events
            FOR EACH ROW
            EXECUTE FUNCTION reject_pipeline_event_mutation()
            """
        )


def downgrade() -> None:
    raise RuntimeError(
        "The operational ledger is forward-only. Restore a database backup "
        "or apply a forward repair instead of deleting audit history."
    )
