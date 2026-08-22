"""Ingestion pipeline for multi-source Arabic news scraping."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import SETTINGS
from src.database.crud import create_raw_article, get_raw_by_hash, get_raw_by_url
from src.database.db import get_db_session
from src.scraping.aljazeera_scraper import AlJazeeraScraper
from src.scraping.bbc_arabic_scraper import BBCArabicScraper
from src.scraping.cnn_arabic_scraper import CNNArabicScraper

logger = logging.getLogger("pipeline.ingest")


def build_scrapers():
    return [
        AlJazeeraScraper(settings=SETTINGS),
        BBCArabicScraper(settings=SETTINGS),
        CNNArabicScraper(settings=SETTINGS),
    ]


def run_ingestion(limit_per_source: int | None = None, write_snapshot: bool = True) -> dict:
    """Scrape all configured sources and write raw records to DB."""
    limit = limit_per_source or SETTINGS.max_articles_per_source
    scrapers = build_scrapers()

    stats: dict[str, int | dict] = {"attempted": 0, "inserted": 0, "sources": {}}
    all_rows: list[dict] = []

    with get_db_session() as session:
        for scraper in scrapers:
            source_inserted = 0
            try:
                articles = scraper.scrape(limit=limit)
                logger.info("Source %s returned %d articles", scraper.source_name, len(articles))
                source_status = "success" if articles else "no_articles"

                for article in articles:
                    stats["attempted"] += 1
                    row = article.to_dict()
                    all_rows.append(row)
                    existing = get_raw_by_url(session, row["url"]) or get_raw_by_hash(
                        session, row["content_hash"]
                    )
                    create_raw_article(session, row)
                    if existing is None:
                        stats["inserted"] += 1
                        source_inserted += 1

                stats["sources"][scraper.source_name] = {
                    "status": source_status,
                    "scraped": len(articles),
                    "inserted": source_inserted,
                    "error": None,
                }
            except Exception as exc:
                logger.exception("Source failure for %s: %s", scraper.source_name, exc)
                stats["sources"][scraper.source_name] = {
                    "status": "failed",
                    "scraped": 0,
                    "inserted": 0,
                    "error": str(exc),
                }

    if write_snapshot:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = Path("data/raw") / f"ingestion_snapshot_{ts}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(all_rows, f, ensure_ascii=False, indent=2, default=str)
        logger.info("Saved ingestion snapshot to %s", out_path)

    return stats
