"""Batch process pipeline from raw to processed article layer."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.database.crud import list_unprocessed_raw_articles, upsert_processed_article
from src.database.db import get_db_session
from src.processing.processing_pipeline import ArticleProcessingPipeline

logger = logging.getLogger("pipeline.process")


def run_processing(batch_size: int = 500, write_snapshot: bool = True) -> dict:
    """Process unprocessed raw records into processed layer."""
    pipeline = ArticleProcessingPipeline()
    processed_rows: list[dict] = []
    processed_count = 0

    with get_db_session() as session:
        raw_articles = list_unprocessed_raw_articles(session, limit=batch_size)
        logger.info("Found %d unprocessed raw articles", len(raw_articles))

        for raw_article in raw_articles:
            try:
                output = pipeline.process(raw_article)
                upsert_processed_article(
                    session=session,
                    raw_article_id=raw_article.id,
                    cleaned_text=output.cleaned_text,
                    topic=output.topic,
                    sentiment_or_escalation=output.sentiment_or_escalation,
                    country_guess=output.country_guess,
                    keyword_matches=output.keyword_matches,
                    ai_summary=output.ai_summary,
                )
                processed_count += 1
                processed_rows.append(
                    {
                        "raw_article_id": raw_article.id,
                        "topic": output.topic,
                        "sentiment_or_escalation": output.sentiment_or_escalation,
                        "country_guess": output.country_guess,
                    }
                )
            except Exception as exc:
                logger.exception("Failed to process raw_article_id=%s: %s", raw_article.id, exc)

    if write_snapshot:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = Path("data/processed") / f"processing_snapshot_{ts}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(processed_rows, f, ensure_ascii=False, indent=2)
        logger.info("Saved processing snapshot to %s", out_path)

    return {
        "processed": processed_count,
        "remaining_after_batch": max(0, len(raw_articles) - processed_count),
    }
