"""Runs the existing rule-based classifiers against documents in the core
schema, writing results as provenanced facts (M1.5 Stage 3b).

Reuses src/processing/'s topic classifier, escalation scorer, and country
gazetteer as-is — no reimplementation. This exists because M1.5 moves
ingest onto src/store's `documents` table, and nothing else writes
`processed_articles` for documents ingested there; without this, the
dashboard's topic/escalation/country panels would have no data source once
ingest cuts over.

Deliberately excludes AI summarization: docs/AGENT_BRIEF.md forbids
LLM-based extraction until a non-LLM baseline exists and is measured — this
pipeline *is* that baseline, not a place to bolt an LLM call onto.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import ExtractorVersion, Fact
from src.core.ontology import Ontology
from src.processing.country_detection import detect_country
from src.processing.escalation_scoring import score_escalation
from src.processing.keyword_classifier import KeywordTopicClassifier
from src.store.blob import BlobStore, get_blob_store
from src.store.database import get_core_session
from src.store.documents import load_document
from src.store.orm import DocumentORM, ExtractorVersionORM, FactORM
from src.store.provenance import record_document_fact, register_extractor_version

EXTRACTOR_NAME = "rule_based_document_classifier"
# 2.0.0 (major): country detection changed from first-keyword-match-wins to
# occurrence counting over a larger, boundary-aware gazetteer, and Sports was
# added as a topic. Those changes altered classifier output.
# 2.0.1 (patch): per-document writes became atomic replacement generations,
# and an undetected country now produces an explicit null leaf. The bump is
# required even though classifier rules did not change: existing 2.0.0 rows
# must be selected once so stale country leaves and duplicate live generations
# are repaired by the new persistence behavior.
EXTRACTOR_VERSION = "2.0.1"

# Fact types this pipeline owns. Used to supersede prior values on
# reprocessing rather than mutating them (P5).
_MANAGED_FACT_TYPES = ("topic", "escalation", "country")

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "config" / "ontology.yaml"


@dataclass(slots=True)
class ProcessCoreStats:
    scanned: int = 0
    processed: int = 0
    errors: int = 0


def _as_fact(row: FactORM) -> Fact:
    """Convert the persistence row used here into the core fact value."""
    return Fact(
        id=row.id,
        fact_type=row.fact_type,
        subject_table=row.subject_table,
        subject_id=row.subject_id,
        payload=row.payload,
        extractor_version_id=row.extractor_version_id,
        supersedes_id=row.supersedes_id,
        retracted=row.retracted,
    )


def _documents_needing_processing(session: Session, limit: int) -> list[int]:
    """Documents lacking a 'topic' fact produced by the CURRENT extractor version.

    One query covers both cases that need work: documents never processed at
    all, and documents processed by an older version whose output is now
    stale. That's the P4 payoff — bump EXTRACTOR_VERSION and the next run
    automatically finds exactly the facts that need regenerating, with no
    migration script and no manual bookkeeping.

    'topic' is the completion marker (not 'escalation' or 'country') because
    process_one_document writes it last — see the comment there.
    """
    up_to_date = (
        select(FactORM.subject_id)
        .join(ExtractorVersionORM, FactORM.extractor_version_id == ExtractorVersionORM.id)
        .where(
            FactORM.subject_table == "documents",
            FactORM.fact_type == "topic",
            FactORM.retracted.is_(False),
            ExtractorVersionORM.name == EXTRACTOR_NAME,
            ExtractorVersionORM.version == EXTRACTOR_VERSION,
        )
    )

    stmt = (
        select(DocumentORM.id)
        .where(DocumentORM.retracted.is_(False))
        .where(DocumentORM.id.notin_(up_to_date))
        .order_by(DocumentORM.collected_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _latest_facts_by_type(session: Session, document_id: int) -> dict[str, Fact]:
    """Most recent non-retracted fact per managed type for one document.

    Fetched in a single query rather than one per fact type — reprocessing
    touches every document, so three queries each would triple the round
    trips against a scale-to-zero database for no benefit.
    """
    rows = session.scalars(
        select(FactORM)
        .where(
            FactORM.subject_table == "documents",
            FactORM.subject_id == document_id,
            FactORM.fact_type.in_(_MANAGED_FACT_TYPES),
            FactORM.retracted.is_(False),
        )
        .order_by(FactORM.created_at.asc(), FactORM.id.asc())
    ).all()

    latest: dict[str, Fact] = {}
    for row in rows:
        # Ascending order means the last write per type wins the dict slot.
        latest[row.fact_type] = _as_fact(row)
    return latest


def process_one_document(
    session: Session,
    document_id: int,
    blob_store: BlobStore,
    ontology: Ontology,
    extractor: ExtractorVersion,
    classifier: KeywordTopicClassifier,
) -> None:
    """Classify one document as an atomic replacement generation.

    ``begin_nested()`` creates a database savepoint. If any classifier or
    provenance write raises, SQLAlchemy rolls back every write made for this
    document while leaving the outer batch transaction usable for the next
    document. The prior generation is retired only after the replacement and
    its provenance have flushed successfully, so the switch is all-or-nothing.
    """
    with session.begin_nested():
        document = load_document(session, document_id, blob_store)

        # Capture every live row before creating replacements. Historical
        # versions of this pipeline could leave more than one live leaf; a
        # successful run heals that state by retiring all of them. The newest
        # row per type remains the direct supersedes link for the audit chain.
        prior_rows = session.scalars(
            select(FactORM)
            .join(
                ExtractorVersionORM,
                FactORM.extractor_version_id == ExtractorVersionORM.id,
            )
            .where(
                FactORM.subject_table == "documents",
                FactORM.subject_id == document_id,
                FactORM.fact_type.in_(_MANAGED_FACT_TYPES),
                FactORM.retracted.is_(False),
                ExtractorVersionORM.name == EXTRACTOR_NAME,
            )
            .order_by(FactORM.created_at.asc(), FactORM.id.asc())
        ).all()
        prior: dict[str, Fact] = {}
        for row in prior_rows:
            prior[row.fact_type] = _as_fact(row)

        detection = detect_country(document.text)
        # None is an explicit current result. Omitting the row would let an old
        # country fact remain the latest leaf when a newer classifier no longer
        # detects a country.
        record_document_fact(
            session,
            document,
            "country",
            detection.country,
            extractor,
            ontology,
            supersedes=prior.get("country"),
        )

        escalation = score_escalation(document.text)
        record_document_fact(
            session,
            document,
            "escalation",
            escalation.label,
            extractor,
            ontology,
            supersedes=prior.get("escalation"),
        )

        # Written last, deliberately: _documents_needing_processing() checks
        # for a current-version 'topic' fact as the completion marker.
        classification = classifier.classify(document.text)
        record_document_fact(
            session,
            document,
            "topic",
            classification.topic,
            extractor,
            ontology,
            supersedes=prior.get("topic"),
        )

        # Force all new facts and provenance rows through database validation
        # before changing which generation is live.
        session.flush()
        for row in prior_rows:
            row.retracted = True
        session.flush()


def run_core_processing(limit: int = 500, blob_store: BlobStore | None = None) -> ProcessCoreStats:
    blob_store = blob_store or get_blob_store()
    ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
    classifier = KeywordTopicClassifier()

    stats = ProcessCoreStats()
    with get_core_session() as session:
        extractor = register_extractor_version(
            session,
            EXTRACTOR_NAME,
            EXTRACTOR_VERSION,
            description="Keyword topic classifier + lexical escalation scoring + occurrence-ranked country gazetteer",
        )

        document_ids = _documents_needing_processing(session, limit=limit)
        stats.scanned = len(document_ids)

        for document_id in document_ids:
            try:
                process_one_document(session, document_id, blob_store, ontology, extractor, classifier)
                stats.processed += 1
            except Exception:
                # process_one_document owns a savepoint, so its partial work
                # is already gone and the outer batch can continue safely.
                stats.errors += 1

    return stats
