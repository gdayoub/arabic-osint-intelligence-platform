"""Frozen SQLAlchemy metadata for the pre-Alembic core schema.

Do not update this module when ``CoreBase`` changes. It is the adoption
contract for revision ``0001_core_baseline``: an unversioned database must
match this historical shape before Alembic may stamp it at revision 0001.
Future application metadata belongs in new migration revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

BASELINE_METADATA = sa.MetaData()
BASELINE_JSON = sa.JSON().with_variant(JSONB(), "postgresql")

sa.Table(
    "extractor_versions",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("version", sa.String(20), nullable=False),
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("name", "version", name="uq_extractor_name_version"),
)

documents = sa.Table(
    "documents",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("source", sa.String(100), nullable=False),
    sa.Column("url", sa.String(1024), unique=True, nullable=True),
    sa.Column("text", sa.Text(), nullable=True),
    sa.Column("text_blob_key", sa.String(255), nullable=True),
    sa.Column("text_sha256", sa.String(64), nullable=True),
    sa.Column("text_length", sa.Integer(), nullable=True),
    sa.Column("content_hash", sa.String(128), nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retracted", sa.Boolean(), nullable=False),
    sa.Column("retracted_reason", sa.Text(), nullable=True),
)
sa.Index("ix_documents_source", documents.c.source)
sa.Index("ix_documents_text_blob_key", documents.c.text_blob_key)
sa.Index("ix_documents_text_sha256", documents.c.text_sha256)
sa.Index("ix_documents_content_hash", documents.c.content_hash)
sa.Index("ix_documents_collected_at", documents.c.collected_at)

mentions = sa.Table(
    "mentions",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "document_id",
        sa.Integer(),
        sa.ForeignKey("documents.id"),
        nullable=False,
    ),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("start_offset", sa.Integer(), nullable=False),
    sa.Column("end_offset", sa.Integer(), nullable=False),
    sa.Column("object_type", sa.String(50), nullable=False),
    sa.Column(
        "extractor_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retracted", sa.Boolean(), nullable=False),
    sa.CheckConstraint("end_offset > start_offset", name="ck_mention_offsets"),
)
sa.Index("ix_mentions_document_id", mentions.c.document_id)

entities = sa.Table(
    "entities",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("object_type", sa.String(50), nullable=False),
    sa.Column("canonical_name", sa.Text(), nullable=False),
    sa.Column("properties", BASELINE_JSON, nullable=False),
    sa.Column("supersedes_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retracted", sa.Boolean(), nullable=False),
    sa.Column("retracted_reason", sa.Text(), nullable=True),
)
sa.Index("ix_entities_object_type", entities.c.object_type)

sa.Table(
    "entity_mentions",
    BASELINE_METADATA,
    sa.Column(
        "entity_id",
        sa.Integer(),
        sa.ForeignKey("entities.id"),
        primary_key=True,
    ),
    sa.Column(
        "mention_id",
        sa.Integer(),
        sa.ForeignKey("mentions.id"),
        primary_key=True,
    ),
)

review_pairs = sa.Table(
    "review_pairs",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "left_mention_id",
        sa.Integer(),
        sa.ForeignKey("mentions.id"),
        nullable=False,
    ),
    sa.Column(
        "right_mention_id",
        sa.Integer(),
        sa.ForeignKey("mentions.id"),
        nullable=False,
    ),
    sa.Column("object_type", sa.String(50), nullable=False),
    sa.Column("score", sa.Float(), nullable=False),
    sa.Column("threshold", sa.Float(), nullable=False),
    sa.Column("features", BASELINE_JSON, nullable=False),
    sa.Column(
        "scorer_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "right_mention_id > left_mention_id",
        name="ck_review_pair_order",
    ),
    sa.UniqueConstraint(
        "left_mention_id",
        "right_mention_id",
        "scorer_version_id",
        name="uq_review_pair_mentions_scorer",
    ),
)
sa.Index("ix_review_pairs_left_mention_id", review_pairs.c.left_mention_id)
sa.Index("ix_review_pairs_right_mention_id", review_pairs.c.right_mention_id)
sa.Index("ix_review_pairs_object_type", review_pairs.c.object_type)
sa.Index("ix_review_pairs_score", review_pairs.c.score)

resolution_decisions = sa.Table(
    "resolution_decisions",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column(
        "review_pair_id",
        sa.Integer(),
        sa.ForeignKey("review_pairs.id"),
        nullable=True,
    ),
    sa.Column(
        "left_mention_id",
        sa.Integer(),
        sa.ForeignKey("mentions.id"),
        nullable=False,
    ),
    sa.Column(
        "right_mention_id",
        sa.Integer(),
        sa.ForeignKey("mentions.id"),
        nullable=False,
    ),
    sa.Column("decision", sa.String(16), nullable=False),
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("reviewer", sa.String(100), nullable=False),
    sa.Column("reason", sa.Text(), nullable=True),
    sa.Column(
        "extractor_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column(
        "supersedes_id",
        sa.Integer(),
        sa.ForeignKey("resolution_decisions.id"),
        nullable=True,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "right_mention_id > left_mention_id",
        name="ck_resolution_decision_pair_order",
    ),
    sa.CheckConstraint(
        "decision IN ('same', 'different')",
        name="ck_resolution_decision_value",
    ),
)
sa.Index(
    "ix_resolution_decisions_review_pair_id",
    resolution_decisions.c.review_pair_id,
)
sa.Index(
    "ix_resolution_decisions_left_mention_id",
    resolution_decisions.c.left_mention_id,
)
sa.Index(
    "ix_resolution_decisions_right_mention_id",
    resolution_decisions.c.right_mention_id,
)
sa.Index(
    "ix_resolution_decisions_pair",
    resolution_decisions.c.left_mention_id,
    resolution_decisions.c.right_mention_id,
)

links = sa.Table(
    "links",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("link_type", sa.String(50), nullable=False),
    sa.Column(
        "from_entity_id",
        sa.Integer(),
        sa.ForeignKey("entities.id"),
        nullable=False,
    ),
    sa.Column(
        "to_entity_id",
        sa.Integer(),
        sa.ForeignKey("entities.id"),
        nullable=False,
    ),
    sa.Column("confidence", sa.Float(), nullable=True),
    sa.Column(
        "extractor_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retracted", sa.Boolean(), nullable=False),
)
sa.Index("ix_links_link_type", links.c.link_type)
sa.Index("ix_links_from_entity_id", links.c.from_entity_id)
sa.Index("ix_links_to_entity_id", links.c.to_entity_id)

facts = sa.Table(
    "facts",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("fact_type", sa.String(50), nullable=False),
    sa.Column("subject_table", sa.String(50), nullable=False),
    sa.Column("subject_id", sa.Integer(), nullable=False),
    sa.Column("payload", BASELINE_JSON, nullable=False),
    sa.Column(
        "extractor_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column("supersedes_id", sa.Integer(), sa.ForeignKey("facts.id"), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retracted", sa.Boolean(), nullable=False),
)
sa.Index("ix_facts_fact_type", facts.c.fact_type)
sa.Index("ix_facts_subject", facts.c.subject_table, facts.c.subject_id)

translations = sa.Table(
    "translations",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("source_sha256", sa.String(64), nullable=False),
    sa.Column("source_lang", sa.String(8), nullable=False),
    sa.Column("target_lang", sa.String(8), nullable=False),
    sa.Column("source_text", sa.Text(), nullable=False),
    sa.Column("translated_text", sa.Text(), nullable=False),
    sa.Column(
        "extractor_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "source_sha256",
        "target_lang",
        name="uq_translation_source_target",
    ),
)
sa.Index("ix_translations_source_sha256", translations.c.source_sha256)

provenance = sa.Table(
    "provenance",
    BASELINE_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("target_table", sa.String(50), nullable=False),
    sa.Column("target_id", sa.Integer(), nullable=False),
    sa.Column(
        "document_id",
        sa.Integer(),
        sa.ForeignKey("documents.id"),
        nullable=False,
    ),
    sa.Column("mention_id", sa.Integer(), sa.ForeignKey("mentions.id"), nullable=True),
    sa.Column(
        "extractor_version_id",
        sa.Integer(),
        sa.ForeignKey("extractor_versions.id"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Index("ix_provenance_target_table", provenance.c.target_table)
sa.Index("ix_provenance_target_id", provenance.c.target_id)

BASELINE_TABLE_NAMES = frozenset(BASELINE_METADATA.tables)
