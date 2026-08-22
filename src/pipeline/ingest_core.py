"""Writes scraped articles into the core schema (documents + facts),
replacing src/pipeline/ingest_pipeline.py's raw_articles path for new
ingestion (M1.5 Stage 4).

src/pipeline/ingest_pipeline.py is left entirely untouched — this file is
additive, not a migration of the old one. Rollback is "run the old command."
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.config.settings import SETTINGS
from src.core.models import Document, ExtractorVersion
from src.core.ontology import Ontology
from src.database.crud import canonicalize_url, compute_content_hash
from src.ops.events import PipelineReasonCode
from src.scraping.alarabiya_scraper import AlArabiyaScraper
from src.scraping.aljazeera_scraper import AlJazeeraScraper
from src.scraping.annahar_scraper import AnNaharScraper
from src.scraping.base_scraper import BaseScraper
from src.scraping.bbc_arabic_scraper import BBCArabicScraper
from src.scraping.cnn_arabic_scraper import CNNArabicScraper
from src.scraping.libyaalahrar_scraper import LibyaAlAhrarScraper
from src.scraping.youm7_scraper import Youm7Scraper
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
        AnNaharScraper(settings=SETTINGS),
        Youm7Scraper(settings=SETTINGS),
        LibyaAlAhrarScraper(settings=SETTINGS),
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


_DEGRADED_SOURCE_REASONS = {
    # A scraper can complete normally while one or more listings/article pages
    # are temporarily unavailable (for example a short-lived 403).  That is
    # an explicit, closed scraper observation, not the same thing as the
    # scraper raising an unexpected exception or a database write failing.
    PipelineReasonCode.SOURCE_FETCH_FAILED,
    PipelineReasonCode.SOURCE_SELECTOR_FAILED,
    PipelineReasonCode.SOURCE_PARSE_FAILED,
    PipelineReasonCode.SOURCE_ZERO_YIELD,
    PipelineReasonCode.DATA_STALE,
}


def _nonnegative_scrape_count(scrape_stats: dict[str, Any], key: str) -> int:
    """Read one scraper counter without trusting arbitrary result types."""
    value = scrape_stats.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _safe_scrape_reason(value: object, *, yielded_count: int) -> PipelineReasonCode | None:
    """Return a closed reason code even if a custom scraper misbehaves."""
    if value is None:
        if yielded_count == 0:
            return PipelineReasonCode.SOURCE_ZERO_YIELD
        return None

    try:
        reason = PipelineReasonCode(value)
    except (TypeError, ValueError):
        return PipelineReasonCode.UNEXPECTED_ERROR

    if yielded_count > 0 and reason == PipelineReasonCode.SOURCE_ZERO_YIELD:
        return PipelineReasonCode.UNEXPECTED_ERROR
    return reason


def _source_status(reason: PipelineReasonCode | None) -> str:
    if reason is None:
        return "success"
    if reason in _DEGRADED_SOURCE_REASONS:
        return "degraded"
    return "failed"


def _latest_publication_time(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_result(
    *,
    scrape_stats: dict[str, Any],
    yielded_count: int,
    inserted_count: int,
    skipped_existing_count: int,
    reason: PipelineReasonCode | None,
    fatal_error_count: int = 0,
) -> dict[str, Any]:
    """Build the public-safe per-source ingestion result.

    The older ``scraped``/``inserted`` keys remain as compatibility aliases.
    The explicit names line up with the operational ledger and make a
    duplicate-only run (yielded > 0, inserted = 0) unambiguous.
    """
    listing_failed = _nonnegative_scrape_count(scrape_stats, "listing_pages_failed")
    article_fetch_failed = _nonnegative_scrape_count(
        scrape_stats, "article_pages_failed"
    )
    selector_failed = _nonnegative_scrape_count(
        scrape_stats, "selector_failure_count"
    )
    parsing_failed = _nonnegative_scrape_count(
        scrape_stats, "parsing_failure_count"
    )

    return {
        "status": _source_status(reason),
        "listing_attempt_count": _nonnegative_scrape_count(
            scrape_stats, "listing_pages_attempted"
        ),
        "listing_zero_link_count": _nonnegative_scrape_count(
            scrape_stats, "listing_pages_without_article_links"
        ),
        "article_attempt_count": _nonnegative_scrape_count(
            scrape_stats, "article_pages_attempted"
        ),
        "article_yield_count": yielded_count,
        "inserted_count": inserted_count,
        "skipped_existing_count": skipped_existing_count,
        "selector_failure_count": selector_failed,
        "parsing_failure_count": parsing_failed,
        "fetch_failure_count": listing_failed + article_fetch_failed,
        "error_count": (
            listing_failed
            + article_fetch_failed
            + selector_failed
            + parsing_failed
            + fatal_error_count
        ),
        "latest_successful_article_at": _latest_publication_time(
            scrape_stats.get("latest_successful_article_at")
        ),
        "reason_code": reason.value if reason is not None else None,
        # Compatibility aliases used by the existing CLI output.
        "scraped": yielded_count,
        "inserted": inserted_count,
        "skipped_existing": skipped_existing_count,
    }


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


def run_core_ingestion(
    limit_per_source: int | None = None,
    blob_store: BlobStore | None = None,
    *,
    on_source_started: Callable[[str], None] | None = None,
    on_source_finished: Callable[[str, dict[str, Any]], None] | None = None,
) -> IngestCoreStats:
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
    for scraper in scrapers:
        # The callbacks are deliberately optional and receive only the
        # already-safe source alias/result. Runtime orchestration uses them
        # to place source ledger boundaries around each scraper. The terminal
        # callback is deliberately after this source's database transaction
        # commits: an event must never say a source succeeded while its rows
        # can still roll back with a later source.
        if on_source_started is not None:
            on_source_started(scraper.source_name)

        articles = []
        scrape_stats: dict[str, Any] = {}
        source_attempted = 0
        source_result: dict[str, Any]

        try:
            articles = scraper.scrape(limit=limit)
            scrape_stats = scraper.get_last_scrape_stats()
            logger.info(
                "Source %s returned %d articles", scraper.source_name, len(articles)
            )
        except Exception:
            # Detailed exceptions stay in the private job log. The returned
            # result is deliberately limited to a closed code.
            logger.exception("Source scrape failed for %s", scraper.source_name)
            try:
                scrape_stats = scraper.get_last_scrape_stats()
            except Exception:
                scrape_stats = {}
            source_result = _source_result(
                scrape_stats=scrape_stats,
                yielded_count=len(articles),
                inserted_count=0,
                skipped_existing_count=0,
                reason=PipelineReasonCode.UNEXPECTED_ERROR,
                fatal_error_count=1,
            )
        else:
            source_inserted = 0
            source_skipped = 0
            try:
                # A committed source is the smallest truthful terminal unit:
                # later sources can fail without undoing earlier successful
                # source telemetry, and a commit failure reports zero rows.
                with get_core_session() as session:
                    extractor_version = register_extractor_version(
                        session, scraper.NAME, scraper.VERSION
                    )
                    for article in articles:
                        source_attempted += 1
                        _, was_new = ingest_article(
                            session,
                            article.to_dict(),
                            blob_store,
                            extractor_version,
                            ontology,
                        )
                        if was_new:
                            source_inserted += 1
                        else:
                            source_skipped += 1
            except Exception:
                # The context manager has already rolled back this source.
                # Do not carry local insert/skip counters into the aggregate
                # result or source terminal summary; they were not committed.
                logger.exception(
                    "Source database write failed for %s", scraper.source_name
                )
                source_result = _source_result(
                    scrape_stats=scrape_stats,
                    yielded_count=len(articles),
                    inserted_count=0,
                    skipped_existing_count=0,
                    reason=PipelineReasonCode.UNEXPECTED_ERROR,
                    fatal_error_count=1,
                )
            else:
                reason = _safe_scrape_reason(
                    scrape_stats.get("reason_code"), yielded_count=len(articles)
                )
                source_result = _source_result(
                    scrape_stats=scrape_stats,
                    yielded_count=len(articles),
                    inserted_count=source_inserted,
                    skipped_existing_count=source_skipped,
                    reason=reason,
                )
                # Count only database effects that committed. Attempts are
                # observed separately below, even if an attempted write
                # later failed and rolled back.
                stats.inserted += source_inserted
                stats.skipped_existing += source_skipped

        stats.attempted += source_attempted
        stats.sources[scraper.source_name] = source_result
        if on_source_finished is not None:
            on_source_finished(scraper.source_name, source_result)

    return stats
