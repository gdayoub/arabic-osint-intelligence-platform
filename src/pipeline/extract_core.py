"""runs the gazetteer over documents and writes mentions to the database.

this is the step that finally fills the mentions table. everything before
it was measuring extraction in isolation. M4 needs actual rows to resolve
into entities so this is the bridge.

it uses the gazetteer and not the transformer. benchmarks/results.md has
the numbers. the model scored 0.89 against the gazetteer 0.90 and costs
2GB of dependencies plus minutes of CI on every run. I am not paying that
for minus 0.01 F1. when the ensemble gets built and measured that decision
gets revisited with a number instead of a feeling.

selection works the same way process_core does. I ask for documents with
no mention from the CURRENT extractor version so bumping the version makes
everything eligible again with no migration script.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import Document, ExtractorVersion, Fact
from src.core.ontology import Ontology
from src.extract.base import MentionExtractor
from src.extract.gazetteer import GazetteerExtractor
from src.store.blob import BlobStore, get_blob_store
from src.store.database import get_core_session
from src.store.documents import load_document
from src.store.orm import DocumentORM, ExtractorVersionORM, FactORM, MentionORM
from src.store.provenance import create_mention, record_document_fact, register_extractor_version

logger = logging.getLogger("pipeline.extract_core")

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "config" / "ontology.yaml"

# fact type that means this document has been through extraction. the value
# is the mention count so it is a useful statistic and not just a flag.
EXTRACTION_MARKER = "mentions_extracted"


@dataclass(slots=True)
class ExtractCoreStats:
    documents_scanned: int = 0
    documents_processed: int = 0
    mentions_written: int = 0
    errors: int = 0


def _as_fact(row: FactORM) -> Fact:
    """Convert an extraction marker row into the core fact value."""
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


def _live_mentions_for_extractor(
    session: Session, document_id: int, extractor_name: str
) -> list[MentionORM]:
    """All live mention rows owned by one extractor across its versions."""
    stmt = (
        select(MentionORM)
        .join(
            ExtractorVersionORM,
            MentionORM.extractor_version_id == ExtractorVersionORM.id,
        )
        .where(
            MentionORM.document_id == document_id,
            MentionORM.retracted.is_(False),
            ExtractorVersionORM.name == extractor_name,
        )
        .order_by(MentionORM.created_at.asc(), MentionORM.id.asc())
    )
    return list(session.scalars(stmt))


def _live_markers_for_extractor(
    session: Session, document_id: int, extractor_name: str
) -> list[FactORM]:
    """All live completion markers owned by one extractor across versions."""
    stmt = (
        select(FactORM)
        .join(
            ExtractorVersionORM,
            FactORM.extractor_version_id == ExtractorVersionORM.id,
        )
        .where(
            FactORM.subject_table == "documents",
            FactORM.subject_id == document_id,
            FactORM.fact_type == EXTRACTION_MARKER,
            FactORM.retracted.is_(False),
            ExtractorVersionORM.name == extractor_name,
        )
        .order_by(FactORM.created_at.asc(), FactORM.id.asc())
    )
    return list(session.scalars(stmt))


def _documents_needing_extraction(
    session: Session, extractor_name: str, extractor_version: str, limit: int
) -> list[int]:
    """documents with no extraction marker from this exact extractor version.

    same shape as process_core. one query covers documents never extracted
    and documents extracted by an older version whose output is now stale.

    I check for the marker fact and not for mention rows. a document can
    legitimately contain zero mentions. a sentence about the central bank
    names nobody. if absence of mentions meant not done then every such
    document would get refetched from R2 and rescanned on every single run
    forever. the marker says scanned and found nothing which is different
    from not scanned.
    """
    done = (
        select(FactORM.subject_id)
        .join(ExtractorVersionORM, FactORM.extractor_version_id == ExtractorVersionORM.id)
        .where(
            FactORM.subject_table == "documents",
            FactORM.fact_type == EXTRACTION_MARKER,
            FactORM.retracted.is_(False),
            ExtractorVersionORM.name == extractor_name,
            ExtractorVersionORM.version == extractor_version,
        )
    )

    stmt = (
        select(DocumentORM.id)
        .where(DocumentORM.retracted.is_(False))
        .where(DocumentORM.id.notin_(done))
        .order_by(DocumentORM.collected_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def extract_one_document(
    session: Session,
    document: Document,
    extractor: MentionExtractor,
    extractor_version: ExtractorVersion,
    ontology: Ontology,
) -> int:
    """Atomically replace one extractor's live mention generation.

    create_mention re-checks that document.text[start:end] equals the
    mention text and raises if it does not. that check is the whole reason
    the alignment work in M2 had to be right. if the offsets were wrong this
    is where it would blow up rather than quietly storing garbage.

    The nested transaction is a database savepoint. A bad span or failed
    provenance write rolls back every new row for this document and restores
    the prior live generation. A successful run retires prior versions from
    this extractor name, while mention generations from other extractors can
    still coexist intentionally.
    """
    with session.begin_nested():
        prior_mentions = _live_mentions_for_extractor(
            session, document.id, extractor_version.name
        )
        prior_markers = _live_markers_for_extractor(
            session, document.id, extractor_version.name
        )
        prior_marker = _as_fact(prior_markers[-1]) if prior_markers else None

        written = 0
        for mention in extractor.extract(document.text):
            create_mention(
                session,
                document=document,
                text=mention.text,
                start=mention.start,
                end=mention.end,
                object_type=mention.object_type,
                extractor_version=extractor_version,
                ontology=ontology,
            )
            written += 1

        # The marker goes last. It is also linked to the previous marker so
        # history stays navigable after the old generation stops being live.
        record_document_fact(
            session,
            document,
            EXTRACTION_MARKER,
            written,
            extractor_version,
            ontology,
            supersedes=prior_marker,
        )

        # Validate the replacement and all provenance before switching the
        # live generation. Both these updates and the inserts are still inside
        # the same savepoint.
        session.flush()
        for row in prior_mentions:
            row.retracted = True
        for row in prior_markers:
            row.retracted = True
        session.flush()

    return written


def run_core_extraction(
    limit: int = 500,
    blob_store: BlobStore | None = None,
    extractor: MentionExtractor | None = None,
) -> ExtractCoreStats:
    blob_store = blob_store or get_blob_store()
    ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
    extractor = extractor or GazetteerExtractor()

    stats = ExtractCoreStats()
    with get_core_session() as session:
        extractor_version = register_extractor_version(
            session,
            extractor.name,
            extractor.version,
            description="Aho-Corasick dictionary match over config/gazetteer.yaml",
        )

        document_ids = _documents_needing_extraction(
            session, extractor.name, extractor.version, limit=limit
        )
        stats.documents_scanned = len(document_ids)

        for document_id in document_ids:
            try:
                document = load_document(session, document_id, blob_store)
                stats.mentions_written += extract_one_document(
                    session, document, extractor, extractor_version, ontology
                )
                stats.documents_processed += 1
            except Exception:
                # one bad document should not lose the other four hundred.
                # a spike here shows up in the stats and is the signal to
                # look rather than something to paper over now.
                logger.exception("extraction failed for document %s", document_id)
                stats.errors += 1

    return stats
