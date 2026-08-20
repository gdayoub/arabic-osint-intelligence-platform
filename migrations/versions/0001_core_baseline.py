"""Baseline the exact pre-Alembic CoreBase schema.

Revision ID: 0001_core_baseline
Revises: None
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_core_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _portable_json() -> sa.TypeEngine:
    """Match PortableJSON: JSON for SQLite and JSONB for PostgreSQL."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "extractor_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_extractor_name_version"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_blob_key", sa.String(length=255), nullable=True),
        sa.Column("text_sha256", sa.String(length=64), nullable=True),
        sa.Column("text_length", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retracted", sa.Boolean(), nullable=False),
        sa.Column("retracted_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("ix_documents_collected_at", "documents", ["collected_at"], unique=False)
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"], unique=False)
    op.create_index("ix_documents_source", "documents", ["source"], unique=False)
    op.create_index("ix_documents_text_blob_key", "documents", ["text_blob_key"], unique=False)
    op.create_index("ix_documents_text_sha256", "documents", ["text_sha256"], unique=False)

    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("properties", _portable_json(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retracted", sa.Boolean(), nullable=False),
        sa.Column("retracted_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["supersedes_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entities_object_type", "entities", ["object_type"], unique=False)

    op.create_table(
        "mentions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retracted", sa.Boolean(), nullable=False),
        sa.CheckConstraint("end_offset > start_offset", name="ck_mention_offsets"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["extractor_version_id"], ["extractor_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mentions_document_id", "mentions", ["document_id"], unique=False)

    op.create_table(
        "entity_mentions",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("mention_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.ForeignKeyConstraint(["mention_id"], ["mentions.id"]),
        sa.PrimaryKeyConstraint("entity_id", "mention_id"),
    )

    op.create_table(
        "review_pairs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("left_mention_id", sa.Integer(), nullable=False),
        sa.Column("right_mention_id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("features", _portable_json(), nullable=False),
        sa.Column("scorer_version_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("right_mention_id > left_mention_id", name="ck_review_pair_order"),
        sa.ForeignKeyConstraint(["left_mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["right_mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["scorer_version_id"], ["extractor_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "left_mention_id",
            "right_mention_id",
            "scorer_version_id",
            name="uq_review_pair_mentions_scorer",
        ),
    )
    op.create_index(
        "ix_review_pairs_left_mention_id",
        "review_pairs",
        ["left_mention_id"],
        unique=False,
    )
    op.create_index("ix_review_pairs_object_type", "review_pairs", ["object_type"], unique=False)
    op.create_index(
        "ix_review_pairs_right_mention_id",
        "review_pairs",
        ["right_mention_id"],
        unique=False,
    )
    op.create_index("ix_review_pairs_score", "review_pairs", ["score"], unique=False)

    op.create_table(
        "resolution_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("review_pair_id", sa.Integer(), nullable=True),
        sa.Column("left_mention_id", sa.Integer(), nullable=False),
        sa.Column("right_mention_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reviewer", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "right_mention_id > left_mention_id",
            name="ck_resolution_decision_pair_order",
        ),
        sa.CheckConstraint(
            "decision IN ('same', 'different')",
            name="ck_resolution_decision_value",
        ),
        sa.ForeignKeyConstraint(["extractor_version_id"], ["extractor_versions.id"]),
        sa.ForeignKeyConstraint(["left_mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["review_pair_id"], ["review_pairs.id"]),
        sa.ForeignKeyConstraint(["right_mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["resolution_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resolution_decisions_left_mention_id",
        "resolution_decisions",
        ["left_mention_id"],
        unique=False,
    )
    op.create_index(
        "ix_resolution_decisions_pair",
        "resolution_decisions",
        ["left_mention_id", "right_mention_id"],
        unique=False,
    )
    op.create_index(
        "ix_resolution_decisions_review_pair_id",
        "resolution_decisions",
        ["review_pair_id"],
        unique=False,
    )
    op.create_index(
        "ix_resolution_decisions_right_mention_id",
        "resolution_decisions",
        ["right_mention_id"],
        unique=False,
    )

    op.create_table(
        "links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("link_type", sa.String(length=50), nullable=False),
        sa.Column("from_entity_id", sa.Integer(), nullable=False),
        sa.Column("to_entity_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retracted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["extractor_version_id"], ["extractor_versions.id"]),
        sa.ForeignKeyConstraint(["from_entity_id"], ["entities.id"]),
        sa.ForeignKeyConstraint(["to_entity_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_links_from_entity_id", "links", ["from_entity_id"], unique=False)
    op.create_index("ix_links_link_type", "links", ["link_type"], unique=False)
    op.create_index("ix_links_to_entity_id", "links", ["to_entity_id"], unique=False)

    op.create_table(
        "facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fact_type", sa.String(length=50), nullable=False),
        sa.Column("subject_table", sa.String(length=50), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("payload", _portable_json(), nullable=False),
        sa.Column("extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retracted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["extractor_version_id"], ["extractor_versions.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["facts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_facts_fact_type", "facts", ["fact_type"], unique=False)
    op.create_index("ix_facts_subject", "facts", ["subject_table", "subject_id"], unique=False)

    op.create_table(
        "translations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_lang", sa.String(length=8), nullable=False),
        sa.Column("target_lang", sa.String(length=8), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column("extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["extractor_version_id"], ["extractor_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_sha256",
            "target_lang",
            name="uq_translation_source_target",
        ),
    )
    op.create_index(
        "ix_translations_source_sha256",
        "translations",
        ["source_sha256"],
        unique=False,
    )

    op.create_table(
        "provenance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("target_table", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("mention_id", sa.Integer(), nullable=True),
        sa.Column("extractor_version_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["extractor_version_id"], ["extractor_versions.id"]),
        sa.ForeignKeyConstraint(["mention_id"], ["mentions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provenance_target_id", "provenance", ["target_id"], unique=False)
    op.create_index("ix_provenance_target_table", "provenance", ["target_table"], unique=False)


def downgrade() -> None:
    raise RuntimeError(
        "The core baseline is forward-only. Restore a database backup or "
        "replace a disposable database instead of dropping evidence tables."
    )
