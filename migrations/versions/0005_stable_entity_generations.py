"""Add stable entity identities and immutable observed resolver generations.

Revision ID: 0005_stable_entity_generations
Revises: 0004_evidence_identity
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_stable_entity_generations"
down_revision: str | None = "0004_evidence_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _portable_json() -> sa.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    sequence_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

    op.create_table(
        "stable_entities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stable_uid", sa.String(length=36), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_uid", name="uq_stable_entities_uid"),
    )
    op.create_index("ix_stable_entities_stable_uid", "stable_entities", ["stable_uid"])
    op.create_index("ix_stable_entities_object_type", "stable_entities", ["object_type"])

    op.create_table(
        "resolver_generations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generation_uid", sa.String(length=36), nullable=False),
        sa.Column("sequence", sequence_type, nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("parent_generation_id", sa.Integer(), nullable=True),
        sa.Column("resolver_extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("reconciler_version", sa.String(length=16), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("constraint_status_counts", _portable_json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('observe')", name="ck_resolver_generation_mode"),
        sa.CheckConstraint("sequence > 0", name="ck_resolver_generation_sequence"),
        sa.ForeignKeyConstraint(
            ["parent_generation_id"], ["resolver_generations.id"]
        ),
        sa.ForeignKeyConstraint(
            ["resolver_extractor_version_id"], ["extractor_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_uid", name="uq_resolver_generations_uid"),
        sa.UniqueConstraint("sequence", name="uq_resolver_generations_sequence"),
    )
    op.create_index(
        "ix_resolver_generations_generation_uid",
        "resolver_generations",
        ["generation_uid"],
    )
    op.create_index(
        "ix_resolver_generations_parent_generation_id",
        "resolver_generations",
        ["parent_generation_id"],
    )
    op.create_index(
        "ix_resolver_generations_resolver_extractor_version_id",
        "resolver_generations",
        ["resolver_extractor_version_id"],
    )

    state_table = op.create_table(
        "stable_entity_resolution_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("active_generation_id", sa.Integer(), nullable=True),
        sa.Column("max_generation_sequence", sequence_type, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "id = 1", name="ck_stable_entity_resolution_state_singleton"
        ),
        sa.CheckConstraint(
            "max_generation_sequence >= 0",
            name="ck_stable_entity_resolution_state_max_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["active_generation_id"], ["resolver_generations.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        state_table,
        [
            {
                "id": 1,
                "active_generation_id": None,
                "max_generation_sequence": 0,
                "updated_at": datetime(1970, 1, 1, tzinfo=timezone.utc),
            }
        ],
    )

    op.create_table(
        "stable_entity_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("stable_entity_id", sa.Integer(), nullable=False),
        sa.Column("source_entity_id", sa.Integer(), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("is_present", sa.Boolean(), nullable=False),
        sa.Column("membership_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "((is_present AND source_entity_id IS NOT NULL) "
            "OR (NOT is_present AND source_entity_id IS NULL))",
            name="ck_stable_entity_snapshot_source_when_present",
        ),
        sa.ForeignKeyConstraint(["generation_id"], ["resolver_generations.id"]),
        sa.ForeignKeyConstraint(["stable_entity_id"], ["stable_entities.id"]),
        sa.ForeignKeyConstraint(["source_entity_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_id",
            "stable_entity_id",
            name="uq_stable_entity_snapshots_generation_entity",
        ),
        sa.UniqueConstraint(
            "id",
            "generation_id",
            name="uq_stable_entity_snapshots_id_generation",
        ),
    )
    op.create_index(
        "ix_stable_entity_snapshots_generation_id",
        "stable_entity_snapshots",
        ["generation_id"],
    )
    op.create_index(
        "ix_stable_entity_snapshots_source_entity_id",
        "stable_entity_snapshots",
        ["source_entity_id"],
    )
    op.create_index(
        "ix_stable_entity_snapshots_entity_generation",
        "stable_entity_snapshots",
        ["stable_entity_id", "generation_id"],
    )

    op.create_table(
        "stable_entity_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("source_mention_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "generation_id"],
            [
                "stable_entity_snapshots.id",
                "stable_entity_snapshots.generation_id",
            ],
            name="fk_stable_entity_memberships_snapshot_generation",
        ),
        sa.ForeignKeyConstraint(["evidence_identity_id"], ["evidence_identities.id"]),
        sa.ForeignKeyConstraint(["source_mention_id"], ["mentions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "evidence_identity_id",
            name="uq_stable_entity_memberships_snapshot_evidence",
        ),
        sa.UniqueConstraint(
            "generation_id",
            "evidence_identity_id",
            name="uq_stable_entity_memberships_generation_evidence",
        ),
        sa.UniqueConstraint(
            "id",
            "evidence_identity_id",
            name="uq_stable_entity_memberships_id_evidence",
        ),
    )
    op.create_index(
        "ix_stable_entity_memberships_snapshot_id",
        "stable_entity_memberships",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_stable_entity_memberships_evidence_identity_id",
        "stable_entity_memberships",
        ["evidence_identity_id"],
    )
    op.create_index(
        "ix_stable_entity_memberships_source_mention_id",
        "stable_entity_memberships",
        ["source_mention_id"],
    )

    op.create_table(
        "stable_entity_lineage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generation_id", sa.Integer(), nullable=False),
        sa.Column("from_stable_entity_id", sa.Integer(), nullable=False),
        sa.Column("to_stable_entity_id", sa.Integer(), nullable=False),
        sa.Column("relationship", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relationship IN ('continued', 'merged_into', 'split_into')",
            name="ck_stable_entity_lineage_relationship",
        ),
        sa.CheckConstraint(
            "((relationship = 'continued' AND from_stable_entity_id = to_stable_entity_id) "
            "OR (relationship IN ('merged_into', 'split_into') "
            "AND from_stable_entity_id <> to_stable_entity_id))",
            name="ck_stable_entity_lineage_endpoints",
        ),
        sa.ForeignKeyConstraint(["generation_id"], ["resolver_generations.id"]),
        sa.ForeignKeyConstraint(["from_stable_entity_id"], ["stable_entities.id"]),
        sa.ForeignKeyConstraint(["to_stable_entity_id"], ["stable_entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_id",
            "from_stable_entity_id",
            "to_stable_entity_id",
            "relationship",
            name="uq_stable_entity_lineage_generation_edge",
        ),
    )
    op.create_index(
        "ix_stable_entity_lineage_generation_id",
        "stable_entity_lineage",
        ["generation_id"],
    )
    op.create_index(
        "ix_stable_entity_lineage_from_generation",
        "stable_entity_lineage",
        ["from_stable_entity_id", "generation_id"],
    )
    op.create_index(
        "ix_stable_entity_lineage_to_generation",
        "stable_entity_lineage",
        ["to_stable_entity_id", "generation_id"],
    )

    op.create_table(
        "stable_entity_lineage_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lineage_id", sa.Integer(), nullable=False),
        sa.Column("evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("source_membership_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lineage_id"], ["stable_entity_lineage.id"]),
        sa.ForeignKeyConstraint(
            ["evidence_identity_id"], ["evidence_identities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_membership_id", "evidence_identity_id"],
            [
                "stable_entity_memberships.id",
                "stable_entity_memberships.evidence_identity_id",
            ],
            name="fk_stable_entity_lineage_evidence_membership_evidence",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lineage_id",
            "evidence_identity_id",
            name="uq_stable_entity_lineage_evidence_edge_evidence",
        ),
    )
    op.create_index(
        "ix_stable_entity_lineage_evidence_lineage_id",
        "stable_entity_lineage_evidence",
        ["lineage_id"],
    )
    op.create_index(
        "ix_stable_entity_lineage_evidence_evidence_identity_id",
        "stable_entity_lineage_evidence",
        ["evidence_identity_id"],
    )
    op.create_index(
        "ix_stable_entity_lineage_evidence_membership",
        "stable_entity_lineage_evidence",
        ["source_membership_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_stable_entity_history_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                    USING ERRCODE = '55000';
            END;
            $function$
            """
        )
        for table_name in (
            "stable_entities",
            "resolver_generations",
            "stable_entity_snapshots",
            "stable_entity_memberships",
            "stable_entity_lineage",
            "stable_entity_lineage_evidence",
        ):
            op.execute(
                "CREATE TRIGGER trg_"
                f"{table_name}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION "
                "reject_stable_entity_history_mutation()"
            )


def downgrade() -> None:
    raise RuntimeError(
        "Stable entity identity is forward-only. Restore a database backup or "
        "apply a forward repair instead of deleting historical generations."
    )
