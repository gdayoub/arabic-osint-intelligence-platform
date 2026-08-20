"""SQLAlchemy ORM mapping for the core entity-resolution schema (M1).

Deliberately separate from src/database/models.py (the existing raw/processed
article tables) — this is a new, additive schema, not a migration of the old
one. See docs/adr/0001-core-persistence-separation.md for why the ORM lives
here in src/store/ rather than in src/core/ alongside the dataclasses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Plain JSON on SQLite (what the unit tests run against), JSONB on Postgres —
# JSONB stores text natively rather than as an escaped string, so it sidesteps
# the ensure_ascii inflation problem for free and is a prerequisite for ever
# indexing inside these columns.
PortableJSON = JSON().with_variant(JSONB, "postgresql")


class CoreBase(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExtractorVersionORM(CoreBase):
    __tablename__ = "extractor_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_extractor_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentORM(CoreBase):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str | None] = mapped_column(String(1024), unique=True, nullable=True)
    # Inline text is a permanent escape hatch (legacy rows, or a row written
    # before blob storage existed) — new rows leave this NULL and use the
    # three columns below instead. See docs/adr/0004-document-text-in-blob-storage.md.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_blob_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text_length: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Unicode code points (P2)
    # Deliberately non-unique: identical content from two different sources
    # is two documents sharing evidence, not a collision to reject — see
    # docs/adr/0006-content-addressed-blob-keys.md.
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    retracted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class MentionORM(CoreBase):
    __tablename__ = "mentions"
    __table_args__ = (CheckConstraint("end_offset > start_offset", name="ck_mention_offsets"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    object_type: Mapped[str] = mapped_column(String(50))
    extractor_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)


class EntityORM(CoreBase):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_type: Mapped[str] = mapped_column(String(50), index=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    properties: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    retracted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class EntityMentionORM(CoreBase):
    __tablename__ = "entity_mentions"

    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    mention_id: Mapped[int] = mapped_column(ForeignKey("mentions.id"), primary_key=True)


class ReviewPairORM(CoreBase):
    """A scored blocking-surviving pair offered for human review.

    Queue rows are immutable model snapshots.  The decision lives in
    ResolutionDecisionORM so changing one's mind appends a new fact instead
    of overwriting the old answer.
    """

    __tablename__ = "review_pairs"
    __table_args__ = (
        CheckConstraint("right_mention_id > left_mention_id", name="ck_review_pair_order"),
        UniqueConstraint(
            "left_mention_id",
            "right_mention_id",
            "scorer_version_id",
            name="uq_review_pair_mentions_scorer",
        ),
        Index("ix_review_pairs_score", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    left_mention_id: Mapped[int] = mapped_column(ForeignKey("mentions.id"), index=True)
    right_mention_id: Mapped[int] = mapped_column(ForeignKey("mentions.id"), index=True)
    object_type: Mapped[str] = mapped_column(String(50), index=True)
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    features: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    scorer_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ResolutionDecisionORM(CoreBase):
    """Append-only human must-link or cannot-link decision for two mentions."""

    __tablename__ = "resolution_decisions"
    __table_args__ = (
        CheckConstraint("right_mention_id > left_mention_id", name="ck_resolution_decision_pair_order"),
        CheckConstraint("decision IN ('same', 'different')", name="ck_resolution_decision_value"),
        Index("ix_resolution_decisions_pair", "left_mention_id", "right_mention_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_pair_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_pairs.id"), nullable=True, index=True
    )
    left_mention_id: Mapped[int] = mapped_column(ForeignKey("mentions.id"), index=True)
    right_mention_id: Mapped[int] = mapped_column(ForeignKey("mentions.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractor_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("resolution_decisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LinkORM(CoreBase):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link_type: Mapped[str] = mapped_column(String(50), index=True)
    from_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    to_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extractor_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)


class FactORM(CoreBase):
    __tablename__ = "facts"
    __table_args__ = (Index("ix_facts_subject", "subject_table", "subject_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fact_type: Mapped[str] = mapped_column(String(50), index=True)
    subject_table: Mapped[str] = mapped_column(String(50))
    subject_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)
    extractor_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)


class TranslationORM(CoreBase):
    """Machine translation cache, keyed by a hash of the SOURCE TEXT rather
    than by whatever object needed it translated.

    Content-addressing is what makes this cheap and durable: identical text
    is translated once no matter how many documents carry it (syndicated
    wire copy is common), and the cache survives entity merges, document
    retraction, and reprocessing, because it was never tied to a row id in
    the first place. See docs/adr/0012-translation-cache.md.
    """

    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint("source_sha256", "target_lang", name="uq_translation_source_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_lang: Mapped[str] = mapped_column(String(8))
    target_lang: Mapped[str] = mapped_column(String(8))
    source_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text)
    extractor_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProvenanceORM(CoreBase):
    __tablename__ = "provenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_table: Mapped[str] = mapped_column(String(50), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    mention_id: Mapped[int | None] = mapped_column(ForeignKey("mentions.id"), nullable=True)
    extractor_version_id: Mapped[int] = mapped_column(ForeignKey("extractor_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
