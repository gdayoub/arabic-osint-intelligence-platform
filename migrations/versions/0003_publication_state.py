"""Add the rebuildable compare-and-publish state singleton.

Revision ID: 0003_publication_state
Revises: 0002_pipeline_event_ledger
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "0003_publication_state"
down_revision: str | None = "0002_pipeline_event_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sequence_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    table = op.create_table(
        "publication_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("current_release_id", sa.String(length=100), nullable=True),
        sa.Column("current_manifest_key", sa.String(length=512), nullable=True),
        sa.Column("current_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("current_data_sequence", sequence_type, nullable=True),
        sa.Column("max_data_sequence_seen", sequence_type, nullable=False),
        sa.Column("promotion_sequence", sequence_type, nullable=False),
        sa.Column("pending_release_id", sa.String(length=100), nullable=True),
        sa.Column("pending_run_id", sa.String(length=100), nullable=True),
        sa.Column("pending_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("pending_manifest_key", sa.String(length=512), nullable=True),
        sa.Column("pending_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("pending_data_sequence", sequence_type, nullable=True),
        sa.Column("pending_promotion_sequence", sequence_type, nullable=True),
        sa.Column(
            "pending_rollback_of_promotion_sequence",
            sequence_type,
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_publication_state_singleton"),
        sa.CheckConstraint(
            "max_data_sequence_seen >= 0",
            name="ck_publication_state_max_data_sequence",
        ),
        sa.CheckConstraint(
            "promotion_sequence >= 0",
            name="ck_publication_state_promotion_sequence",
        ),
        sa.CheckConstraint(
            "current_data_sequence IS NULL "
            "OR current_data_sequence <= max_data_sequence_seen",
            name="ck_publication_state_current_below_high_water",
        ),
        sa.CheckConstraint(
            "current_data_sequence IS NULL OR current_data_sequence > 0",
            name="ck_publication_state_current_data_positive",
        ),
        sa.CheckConstraint(
            "pending_data_sequence IS NULL OR pending_data_sequence > 0",
            name="ck_publication_state_pending_data_positive",
        ),
        sa.CheckConstraint(
            "pending_promotion_sequence IS NULL "
            "OR pending_promotion_sequence > promotion_sequence",
            name="ck_publication_state_pending_promotion_newer",
        ),
        sa.CheckConstraint(
            "pending_rollback_of_promotion_sequence IS NULL "
            "OR pending_rollback_of_promotion_sequence = promotion_sequence",
            name="ck_publication_state_rollback_targets_current",
        ),
        sa.CheckConstraint(
            "((current_release_id IS NULL AND current_manifest_key IS NULL "
            "AND current_manifest_sha256 IS NULL AND current_data_sequence IS NULL) "
            "OR (current_release_id IS NOT NULL AND current_manifest_key IS NOT NULL "
            "AND current_manifest_sha256 IS NOT NULL "
            "AND current_data_sequence IS NOT NULL))",
            name="ck_publication_state_current_complete",
        ),
        sa.CheckConstraint(
            "((pending_release_id IS NULL AND pending_run_id IS NULL "
            "AND pending_commit_sha IS NULL AND pending_manifest_key IS NULL "
            "AND pending_manifest_sha256 IS NULL AND pending_data_sequence IS NULL "
            "AND pending_promotion_sequence IS NULL "
            "AND pending_rollback_of_promotion_sequence IS NULL) "
            "OR (pending_release_id IS NOT NULL AND pending_run_id IS NOT NULL "
            "AND pending_commit_sha IS NOT NULL AND pending_manifest_key IS NOT NULL "
            "AND pending_manifest_sha256 IS NOT NULL "
            "AND pending_data_sequence IS NOT NULL "
            "AND pending_promotion_sequence IS NOT NULL))",
            name="ck_publication_state_pending_complete",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": 1,
                "max_data_sequence_seen": 0,
                "promotion_sequence": 0,
                "updated_at": datetime(1970, 1, 1, tzinfo=timezone.utc),
            }
        ],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Publication state is replaced by a forward repair. Restore a database "
        "backup instead of removing the promotion coordination boundary."
    )
