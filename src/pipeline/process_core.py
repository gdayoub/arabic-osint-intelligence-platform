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

from src.core.models import ExtractorVersion
from src.core.ontology import Ontology
from src.processing.escalation_scoring import score_escalation
from src.processing.keyword_classifier import KeywordTopicClassifier
from src.processing.processing_pipeline import ArticleProcessingPipeline
from src.store.blob import BlobStore, get_blob_store
from src.store.database import get_core_session
from src.store.documents import load_document
from src.store.orm import DocumentORM, FactORM
from src.store.provenance import record_document_fact, register_extractor_version

EXTRACTOR_NAME = "rule_based_document_classifier"
EXTRACTOR_VERSION = "1.0.0"

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "config" / "ontology.yaml"


@dataclass(slots=True)
class ProcessCoreStats:
    scanned: int = 0
    processed: int = 0
    errors: int = 0


def _unprocessed_document_ids(session: Session, limit: int) -> list[int]:
    """Documents with no 'topic' fact yet.

    Same anti-join shape as the legacy src/database/crud.py's
    list_unprocessed_raw_articles — a LEFT JOIN plus IS NULL, not a
    "NOT IN (subquery)", which degrades badly as the facts table grows.
    'topic' specifically (not 'escalation' or 'country') is the completion
    marker because process_one_document() writes it last — see there for why.
    """
    stmt = (
        select(DocumentORM.id)
        .outerjoin(
            FactORM,
            (FactORM.subject_table == "documents")
            & (FactORM.subject_id == DocumentORM.id)
            & (FactORM.fact_type == "topic"),
        )
        .where(FactORM.id.is_(None))
        .where(DocumentORM.retracted.is_(False))
        .order_by(DocumentORM.collected_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def process_one_document(
    session: Session,
    document_id: int,
    blob_store: BlobStore,
    ontology: Ontology,
    extractor: ExtractorVersion,
    classifier: KeywordTopicClassifier,
    country_guesser: ArticleProcessingPipeline,
) -> None:
    document = load_document(session, document_id, blob_store)

    country = country_guesser.guess_country(document.text)
    if country is not None:
        record_document_fact(session, document, "country", country, extractor, ontology)

    escalation = score_escalation(document.text)
    record_document_fact(session, document, "escalation", escalation.label, extractor, ontology)

    # Written last, deliberately: _unprocessed_document_ids() checks for a
    # 'topic' fact as the "this document is done" signal. If country or
    # escalation somehow failed above, the document would raise before
    # reaching here and topic would never be written — so it stays eligible
    # for reprocessing next run instead of silently looking complete.
    classification = classifier.classify(document.text)
    record_document_fact(session, document, "topic", classification.topic, extractor, ontology)


def run_core_processing(limit: int = 500, blob_store: BlobStore | None = None) -> ProcessCoreStats:
    blob_store = blob_store or get_blob_store()
    ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
    classifier = KeywordTopicClassifier()
    country_guesser = ArticleProcessingPipeline()  # only .guess_country() is used; no ai_summarizer call happens

    stats = ProcessCoreStats()
    with get_core_session() as session:
        extractor = register_extractor_version(
            session,
            EXTRACTOR_NAME,
            EXTRACTOR_VERSION,
            description="Keyword topic classifier + lexical escalation scoring + country gazetteer, reused from src/processing/",
        )

        document_ids = _unprocessed_document_ids(session, limit=limit)
        stats.scanned = len(document_ids)

        for document_id in document_ids:
            try:
                process_one_document(
                    session, document_id, blob_store, ontology, extractor, classifier, country_guesser
                )
                stats.processed += 1
            except Exception:
                # These are pure functions over already-validated data (no
                # network, no unvalidated user input), so a failure here is
                # expected to be rare. If one ever corrupts the session
                # enough to break subsequent iterations, that will show up
                # as a spike in `errors` on the next run and is the signal
                # to add per-document sub-transactions — not worth the
                # complexity until it's an observed problem (P7).
                stats.errors += 1

    return stats
