"""Add durable document/evidence identity and resolution constraints.

Revision ID: 0004_evidence_identity
Revises: 0003_publication_state
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_evidence_identity"
down_revision: str | None = "0003_publication_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("document_uid", sa.String(length=36), nullable=False),
        sa.Column("identity_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uq_document_identities_document"),
        sa.UniqueConstraint("document_uid", name="uq_document_identities_uid"),
    )

    op.create_table(
        "evidence_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_identity_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=71), nullable=False),
        sa.Column("identity_version", sa.String(length=16), nullable=False),
        sa.Column("source_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_offset > start_offset", name="ck_evidence_identity_offsets"),
        sa.ForeignKeyConstraint(["document_identity_id"], ["document_identities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_evidence_identities_fingerprint"),
        sa.UniqueConstraint(
            "document_identity_id",
            "source_text_sha256",
            "start_offset",
            "end_offset",
            "object_type",
            "language",
            name="uq_evidence_identities_signature",
        ),
    )
    op.create_index(
        "ix_evidence_identities_document_identity_id",
        "evidence_identities",
        ["document_identity_id"],
    )
    op.create_index(
        "ix_evidence_identities_source_text_sha256",
        "evidence_identities",
        ["source_text_sha256"],
    )
    op.create_index(
        "ix_evidence_identities_object_type",
        "evidence_identities",
        ["object_type"],
    )
    op.create_index(
        "ix_evidence_identities_language",
        "evidence_identities",
        ["language"],
    )
    op.create_index(
        "ix_evidence_identities_document_span",
        "evidence_identities",
        ["document_identity_id", "start_offset", "end_offset"],
    )

    op.create_table(
        "mention_evidence_identities",
        sa.Column("mention_id", sa.Integer(), nullable=False),
        sa.Column("evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("mapper_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evidence_identity_id"], ["evidence_identities.id"]),
        sa.ForeignKeyConstraint(["mention_id"], ["mentions.id"]),
        sa.PrimaryKeyConstraint("mention_id"),
    )
    op.create_index(
        "ix_mention_evidence_identities_evidence_identity_id",
        "mention_evidence_identities",
        ["evidence_identity_id"],
    )

    op.create_table(
        "resolution_constraints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_decision_id", sa.Integer(), nullable=False),
        sa.Column("left_evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("right_evidence_identity_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("supersedes_constraint_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('same', 'different')",
            name="ck_resolution_constraint_value",
        ),
        sa.ForeignKeyConstraint(
            ["left_evidence_identity_id"], ["evidence_identities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["right_evidence_identity_id"], ["evidence_identities.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_decision_id"], ["resolution_decisions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_constraint_id"], ["resolution_constraints.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_decision_id",
            name="uq_resolution_constraints_source_decision",
        ),
    )
    op.create_index(
        "ix_resolution_constraints_left_evidence_identity_id",
        "resolution_constraints",
        ["left_evidence_identity_id"],
    )
    op.create_index(
        "ix_resolution_constraints_right_evidence_identity_id",
        "resolution_constraints",
        ["right_evidence_identity_id"],
    )
    op.create_index(
        "ix_resolution_constraints_supersedes_constraint_id",
        "resolution_constraints",
        ["supersedes_constraint_id"],
    )
    op.create_index(
        "ix_resolution_constraints_evidence_pair",
        "resolution_constraints",
        ["left_evidence_identity_id", "right_evidence_identity_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Evidence identity is forward-only. Restore a database backup or apply "
        "a forward repair instead of deleting durable constraint lineage."
    )
