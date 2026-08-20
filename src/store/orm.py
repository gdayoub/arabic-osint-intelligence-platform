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
    BigInteger,
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
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.ops.events import PIPELINE_EVENT_TYPES, PIPELINE_REASON_CODES

# Plain JSON on SQLite (what the unit tests run against), JSONB on Postgres —
# JSONB stores text natively rather than as an escaped string, so it sidesteps
# the ensure_ascii inflation problem for free and is a prerequisite for ever
# indexing inside these columns.
PortableJSON = JSON().with_variant(JSONB, "postgresql")
PortableEventId = BigInteger().with_variant(Integer, "sqlite")


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


def _quoted_values(values: tuple[str, ...]) -> str:
    """Render fixed Python constants as a SQL CHECK list."""
    return ", ".join(f"'{value}'" for value in values)


class PipelineEventORM(CoreBase):
    """One immutable observation about a pipeline run.

    A run's terminal state cannot safely be stored by updating its start row:
    a killed process cannot perform that update.  Start, heartbeat, terminal,
    stage, and source observations are therefore separate append-only rows.
    Current health is reconstructed from the ordered stream.
    """

    __tablename__ = "pipeline_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({_quoted_values(PIPELINE_EVENT_TYPES)})",
            name="ck_pipeline_event_type",
        ),
        CheckConstraint(
            "reason_code IS NULL OR "
            f"reason_code IN ({_quoted_values(PIPELINE_REASON_CODES)})",
            name="ck_pipeline_event_reason_code",
        ),
        CheckConstraint(
            "input_count IS NULL OR input_count >= 0",
            name="ck_pipeline_event_input_count",
        ),
        CheckConstraint(
            "output_count IS NULL OR output_count >= 0",
            name="ck_pipeline_event_output_count",
        ),
        CheckConstraint(
            "error_count IS NULL OR error_count >= 0",
            name="ck_pipeline_event_error_count",
        ),
        CheckConstraint(
            "attempt_count IS NULL OR attempt_count >= 0",
            name="ck_pipeline_event_attempt_count",
        ),
        CheckConstraint(
            "inserted_count IS NULL OR inserted_count >= 0",
            name="ck_pipeline_event_inserted_count",
        ),
        CheckConstraint(
            "selector_failure_count IS NULL OR selector_failure_count >= 0",
            name="ck_pipeline_event_selector_failure_count",
        ),
        CheckConstraint(
            "parsing_failure_count IS NULL OR parsing_failure_count >= 0",
            name="ck_pipeline_event_parsing_failure_count",
        ),
        CheckConstraint(
            "data_sequence IS NULL OR data_sequence > 0",
            name="ck_pipeline_event_data_sequence",
        ),
        CheckConstraint(
            "promotion_sequence IS NULL OR promotion_sequence > 0",
            name="ck_pipeline_event_promotion_sequence",
        ),
        CheckConstraint(
            "rollback_of_promotion_sequence IS NULL "
            "OR rollback_of_promotion_sequence > 0",
            name="ck_pipeline_event_rollback_promotion_sequence",
        ),
        UniqueConstraint("event_key", name="uq_pipeline_events_event_key"),
        Index("ix_pipeline_events_run_time", "run_id", "occurred_at", "id"),
        Index("ix_pipeline_events_stage", "stage", "occurred_at"),
        Index("ix_pipeline_events_source", "source", "occurred_at"),
        Index("ix_pipeline_events_data_sequence", "data_sequence"),
        Index("ix_pipeline_events_promotion_sequence", "promotion_sequence"),
    )

    # PostgreSQL BIGINT leaves ample room for a long-lived event stream.  The
    # SQLite variant must be exactly INTEGER for rowid autoincrement semantics.
    id: Mapped[int] = mapped_column(PortableEventId, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(180))
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    release_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    commit_sha: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)

    input_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inserted_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selector_failure_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parsing_failure_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_successful_article_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    extractor_versions: Mapped[dict[str, str]] = mapped_column(PortableJSON, default=dict)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_sequence: Mapped[int | None] = mapped_column(PortableEventId, nullable=True)
    promotion_sequence: Mapped[int | None] = mapped_column(PortableEventId, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_of_promotion_sequence: Mapped[int | None] = mapped_column(
        PortableEventId, nullable=True
    )


class PublicationStateORM(CoreBase):
    """Rebuildable singleton containing the publication high-water marks.

    Unlike ``PipelineEventORM``, this row is intentionally mutable.  It is a
    compare-and-publish coordination record and cache; the event stream and
    immutable manifests remain the audit source of truth.
    """

    __tablename__ = "publication_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_publication_state_singleton"),
        CheckConstraint(
            "max_data_sequence_seen >= 0",
            name="ck_publication_state_max_data_sequence",
        ),
        CheckConstraint(
            "promotion_sequence >= 0",
            name="ck_publication_state_promotion_sequence",
        ),
        CheckConstraint(
            "current_data_sequence IS NULL "
            "OR current_data_sequence <= max_data_sequence_seen",
            name="ck_publication_state_current_below_high_water",
        ),
        CheckConstraint(
            "current_data_sequence IS NULL OR current_data_sequence > 0",
            name="ck_publication_state_current_data_positive",
        ),
        CheckConstraint(
            "pending_data_sequence IS NULL OR pending_data_sequence > 0",
            name="ck_publication_state_pending_data_positive",
        ),
        CheckConstraint(
            "pending_promotion_sequence IS NULL "
            "OR pending_promotion_sequence > promotion_sequence",
            name="ck_publication_state_pending_promotion_newer",
        ),
        CheckConstraint(
            "pending_rollback_of_promotion_sequence IS NULL "
            "OR pending_rollback_of_promotion_sequence = promotion_sequence",
            name="ck_publication_state_rollback_targets_current",
        ),
        CheckConstraint(
            "((current_release_id IS NULL AND current_manifest_key IS NULL "
            "AND current_manifest_sha256 IS NULL AND current_data_sequence IS NULL) "
            "OR (current_release_id IS NOT NULL AND current_manifest_key IS NOT NULL "
            "AND current_manifest_sha256 IS NOT NULL "
            "AND current_data_sequence IS NOT NULL))",
            name="ck_publication_state_current_complete",
        ),
        CheckConstraint(
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    current_release_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_manifest_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    current_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_data_sequence: Mapped[int | None] = mapped_column(
        PortableEventId, nullable=True
    )
    max_data_sequence_seen: Mapped[int] = mapped_column(PortableEventId, default=0)
    promotion_sequence: Mapped[int] = mapped_column(PortableEventId, default=0)

    pending_release_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pending_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pending_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_manifest_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pending_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_data_sequence: Mapped[int | None] = mapped_column(
        PortableEventId, nullable=True
    )
    pending_promotion_sequence: Mapped[int | None] = mapped_column(
        PortableEventId, nullable=True
    )
    pending_rollback_of_promotion_sequence: Mapped[int | None] = mapped_column(
        PortableEventId, nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class AppendOnlyEventError(RuntimeError):
    """Raised when ORM code tries to mutate an operational event."""


@event.listens_for(PipelineEventORM, "before_update")
def _reject_pipeline_event_update(_mapper, _connection, _target) -> None:  # noqa: ANN001
    raise AppendOnlyEventError("pipeline_events is append-only; append a new event")


@event.listens_for(PipelineEventORM, "before_delete")
def _reject_pipeline_event_delete(_mapper, _connection, _target) -> None:  # noqa: ANN001
    raise AppendOnlyEventError("pipeline_events is append-only; events cannot be deleted")
