"""FastAPI REST API exposing OSINT intelligence data for external consumers (e.g. portfolio)."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from src.config.logging_config import setup_logging
from src.database.db import get_db_session
from src.database.models import ProcessedArticle, RawArticle

setup_logging()
logger = logging.getLogger("api")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Arabic OSINT Intelligence API",
    description="Live intelligence data from the Arabic OSINT platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://george-dayoub-portfolio.vercel.app",
        "https://osint-app-production-c0b4.up.railway.app",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=2)


@app.get("/dashboard")
def serve_dashboard():
    """Serve the premium intelligence dashboard."""
    return FileResponse(STATIC_DIR / "dashboard.html", media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/stats")
def get_stats():
    """Total article counts and per-source breakdown."""
    with get_db_session() as session:
        total_raw = session.execute(
            select(func.count()).select_from(RawArticle)
        ).scalar() or 0

        total_processed = session.execute(
            select(func.count()).select_from(ProcessedArticle)
        ).scalar() or 0

        source_rows = session.execute(
            select(RawArticle.source, func.count().label("count"))
            .group_by(RawArticle.source)
            .order_by(func.count().desc())
        ).all()

        return {
            "total_raw": total_raw,
            "total_processed": total_processed,
            "sources": {r.source: r.count for r in source_rows},
        }


@app.get("/api/recent")
def get_recent(limit: int = 10):
    """Most recently processed articles with full metadata."""
    limit = min(limit, 50)
    with get_db_session() as session:
        rows = session.execute(
            select(ProcessedArticle, RawArticle)
            .join(RawArticle, ProcessedArticle.raw_article_id == RawArticle.id)
            .order_by(ProcessedArticle.processed_at.desc())
            .limit(limit)
        ).all()

        return [
            {
                "title": raw.title,
                "source": raw.source,
                "url": raw.url,
                "topic": proc.topic,
                "escalation": proc.sentiment_or_escalation,
                "country": proc.country_guess,
                "ai_summary": proc.ai_summary,
                "processed_at": proc.processed_at.isoformat(),
                "published_date": raw.published_date.isoformat() if raw.published_date else None,
            }
            for proc, raw in rows
        ]


@app.get("/api/topics")
def get_topics():
    """Topic distribution across all processed articles."""
    with get_db_session() as session:
        rows = session.execute(
            select(ProcessedArticle.topic, func.count().label("count"))
            .group_by(ProcessedArticle.topic)
            .order_by(func.count().desc())
        ).all()

        return {"topics": [{"topic": r.topic, "count": r.count} for r in rows]}


@app.get("/api/escalation")
def get_escalation():
    """Escalation level breakdown across all processed articles."""
    with get_db_session() as session:
        rows = session.execute(
            select(ProcessedArticle.sentiment_or_escalation, func.count().label("count"))
            .group_by(ProcessedArticle.sentiment_or_escalation)
        ).all()

        return {"escalation": {r.sentiment_or_escalation: r.count for r in rows}}


@app.post("/api/trigger-pipeline")
async def trigger_pipeline(authorization: Optional[str] = Header(None)):
    """Trigger the ingestion + processing pipeline in the background.

    Protected by a bearer token (PIPELINE_TRIGGER_TOKEN env var).
    Called by GitHub Actions cron every 6 hours.
    """
    expected_token = os.getenv("PIPELINE_TRIGGER_TOKEN", "")
    if not expected_token or authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_pipeline_sync)
    return {"status": "pipeline_triggered"}


def _run_pipeline_sync() -> None:
    """Run the full pipeline synchronously (called from thread pool)."""
    try:
        from src.pipeline.ingest_pipeline import run_ingestion
        from src.pipeline.process_pipeline import run_processing

        logger.info("Pipeline trigger: starting ingestion")
        ingest_stats = run_ingestion(write_snapshot=False)
        logger.info("Ingestion complete: %s", ingest_stats)

        logger.info("Pipeline trigger: starting processing")
        process_stats = run_processing(write_snapshot=False)
        logger.info("Processing complete: %s", process_stats)
    except Exception as exc:
        logger.exception("Pipeline trigger failed: %s", exc)
