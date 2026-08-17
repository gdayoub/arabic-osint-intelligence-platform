"""Writes scraped articles into the core schema (documents + facts),
replacing src/pipeline/ingest_pipeline.py's raw_articles path for new
ingestion (M1.5 Stage 4).

src/pipeline/ingest_pipeline.py is left entirely untouched — this file is
additive, not a migration of the old one. Rollback is "run the old command."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.config.settings import SETTINGS
from src.core.models import Document, ExtractorVersion
from src.core.ontology import Ontology
from src.database.crud import canonicalize_url, compute_content_hash
from src.scraping.alarabiya_scraper import AlArabiyaScraper
from src.scraping.aljazeera_scraper import AlJazeeraScraper
from src.scraping.base_scraper import BaseScraper
from src.scraping.bbc_arabic_scraper import BBCArabicScraper
from src.scraping.cnn_arabic_scraper import CNNArabicScraper
from src.store.blob import BlobStore, get_blob_store
from src.store.database import get_core_session
from src.store.documents import get_document_ref_by_url
from src.store.provenance import create_document, record_document_fact, register_extractor_version

logger = logging.getLogger("pipeline.ingest_core")

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "config" / "ontology.yaml"

# Article fields with a home in config/ontology.yaml's document_attributes —
# recorded as facts (ADR 0007), not columns, because they're news-specific
# and documents.* must stay domain-agnostic (P3).
_METADATA_FACT_FIELDS = ("title", "subtitle", "author", "tags", "source_section")


def build_scrapers() -> list[BaseScraper]:
    return [
        AlJazeeraScraper(settings=SETTINGS),
        BBCArabicScraper(settings=SETTINGS),
        CNNArabicScraper(settings=SETTINGS),
        AlArabiyaScraper(settings=SETTINGS),
    ]


def article_to_document_text(article: dict[str, Any]) -> str:
    """Compose the text a Mention's character offsets are measured against.

    LOAD-BEARING FOR P2: every offset ever stored is an index into whatever
    this function returns. Changing the separator, the field order, or which
    fields are included invalidates every mention offset stored before the
    change — treat any edit here as a P4 major-version bump on every
    scraper's extractor version, not a routine tweak. Guarded by
    tests/golden/document_text_composition.json — if that test fails after
    an intentional change, the golden file needs updating too, deliberately.

    Title and subtitle are included (not just body) because they're dense
    with entity names, and a mention can only be recorded at an offset into
    this string — a title excluded here is a title nothing can ever extract
    a mention from.
    """
    parts = [part for part in (article.get("title"), article.get("subtitle"), article.get("body")) if part]
    return "\n\n".join(parts)


@dataclass(slots=True)
class IngestCoreStats:
    attempted: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


def ingest_article(
    session: Session,
    article: dict[str, Any],
    blob_store: BlobStore,
    extractor_version: ExtractorVersion,
    ontology: Ontology,
) -> tuple[Document, bool]:
    """Insert one scraped article as a document + metadata facts.

    Returns (document, was_new) — was_new is False if a document already
    exists at this URL, in which case nothing is written (dedup on URL only,
    not on content_hash: unlike legacy raw_articles, the same wire story
    syndicated by two different sources should become two documents sharing
    a blob, not one document that silently drops the second source as
    evidence — see docs/adr/0006-content-addressed-blob-keys.md).
    """
    canonical_url = canonicalize_url(article["url"])
    existing = get_document_ref_by_url(session, canonical_url)
    if existing is not None:
        return existing, False

    text = article_to_document_text(article)
    content_hash = article.get("content_hash") or compute_content_hash(
        article["source"], article.get("title", ""), article.get("body", ""), canonical_url
    )

    document = create_document(
        session,
        source=article["source"],
        text=text,
        content_hash=content_hash,
        blob_store=blob_store,
        url=canonical_url,
        published_at=article.get("published_date"),
        collected_at=article.get("collected_at"),
    )

    for field_name in _METADATA_FACT_FIELDS:
        value = article.get(field_name)
        if value:  # skip None/empty — no fact for a field the scraper didn't find
            record_document_fact(session, document, field_name, value, extractor_version, ontology)

    return document, True


def run_core_ingestion(limit_per_source: int | None = None, blob_store: BlobStore | None = None) -> IngestCoreStats:
    """Scrape all configured sources and write documents to the core schema.

    Deliberately does not write data/raw/ingestion_snapshot_*.json the way
    ingest_pipeline.py does — that snapshot existed to keep a durable copy
    of scraped text, which the blob store now does natively, deduplicated
    and compressed, without a growing pile of unmanaged JSON files.
    """
    limit = limit_per_source or SETTINGS.max_articles_per_source
    blob_store = blob_store or get_blob_store()
    ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
    scrapers = build_scrapers()

    stats = IngestCoreStats()
    with get_core_session() as session:
        for scraper in scrapers:
            extractor_version = register_extractor_version(session, scraper.NAME, scraper.VERSION)
            source_inserted = 0
            source_skipped = 0
            try:
                articles = scraper.scrape(limit=limit)
                logger.info("Source %s returned %d articles", scraper.source_name, len(articles))

                for article in articles:
                    stats.attempted += 1
                    _, was_new = ingest_article(session, article.to_dict(), blob_store, extractor_version, ontology)
                    if was_new:
                        stats.inserted += 1
                        source_inserted += 1
                    else:
                        stats.skipped_existing += 1
                        source_skipped += 1

                stats.sources[scraper.source_name] = {
                    "status": "success" if articles else "no_articles",
                    "scraped": len(articles),
                    "inserted": source_inserted,
                    "skipped_existing": source_skipped,
                    "error": None,
                }
            except Exception as exc:
                logger.exception("Source failure for %s: %s", scraper.source_name, exc)
                stats.sources[scraper.source_name] = {
                    "status": "failed",
                    "scraped": 0,
                    "inserted": source_inserted,
                    "skipped_existing": source_skipped,
                    "error": str(exc),
                }

    return stats
