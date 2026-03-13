"""CRUD helpers for ingestion and processing pipelines."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.database.models import ProcessedArticle, RawArticle


def compute_content_hash(source: str, title: str, body: str, url: str) -> str:
    """Hash fields likely to identify duplicate content across runs."""
    payload = f"{source}|{title}|{body}|{url}".strip().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    split = urlsplit(url.strip())
    path = split.path.rstrip("/") or "/"
    return urlunsplit((split.scheme, split.netloc.lower(), path, "", ""))


def get_raw_by_url(session: Session, url: str) -> RawArticle | None:
    stmt: Select[tuple[RawArticle]] = select(RawArticle).where(RawArticle.url == url)
    return session.execute(stmt).scalar_one_or_none()


def get_raw_by_hash(session: Session, content_hash: str) -> RawArticle | None:
    stmt: Select[tuple[RawArticle]] = select(RawArticle).where(RawArticle.content_hash == content_hash)
    return session.execute(stmt).scalar_one_or_none()


def create_raw_article(session: Session, article_data: dict[str, Any]) -> RawArticle:
    """Insert a raw article if it does not exist by URL/hash."""
    canonical_url = canonicalize_url(article_data["url"])
    content_hash = article_data.get("content_hash") or compute_content_hash(
        article_data["source"],
        article_data.get("title", ""),
        article_data.get("body", ""),
        canonical_url,
    )

    existing = get_raw_by_url(session, canonical_url) or get_raw_by_hash(session, content_hash)
    if existing:
        return existing

    raw = RawArticle(
        source=article_data["source"],
        title=article_data.get("title", ""),
        subtitle=article_data.get("subtitle"),
        body=article_data.get("body", ""),
        author=article_data.get("author"),
        published_date=article_data.get("published_date"),
        url=canonical_url,
        tags=article_data.get("tags") or [],
        source_section=article_data.get("source_section"),
        collected_at=article_data.get("collected_at") or datetime.now(timezone.utc),
        content_hash=content_hash,
    )
    session.add(raw)
    session.flush()
    return raw


def list_unprocessed_raw_articles(session: Session, limit: int = 500) -> list[RawArticle]:
    stmt: Select[tuple[RawArticle]] = (
        select(RawArticle)
        .outerjoin(ProcessedArticle, ProcessedArticle.raw_article_id == RawArticle.id)
        .where(ProcessedArticle.id.is_(None))
        .order_by(RawArticle.collected_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def upsert_processed_article(
    session: Session,
    raw_article_id: int,
    cleaned_text: str,
    topic: str,
    sentiment_or_escalation: str,
    country_guess: str | None,
    keyword_matches: dict[str, Any] | None,
    ml_confidence: float | None = None,
) -> ProcessedArticle:
    """Create or update processed record for a raw article."""
    stmt: Select[tuple[ProcessedArticle]] = select(ProcessedArticle).where(
        ProcessedArticle.raw_article_id == raw_article_id
    )
    existing = session.execute(stmt).scalar_one_or_none()

    if existing:
        existing.cleaned_text = cleaned_text
        existing.topic = topic
        existing.sentiment_or_escalation = sentiment_or_escalation
        existing.country_guess = country_guess
        existing.keyword_matches = keyword_matches
        existing.ml_confidence = ml_confidence
        existing.processed_at = datetime.now(timezone.utc)
        return existing

    processed = ProcessedArticle(
        raw_article_id=raw_article_id,
        cleaned_text=cleaned_text,
        topic=topic,
        sentiment_or_escalation=sentiment_or_escalation,
        country_guess=country_guess,
        keyword_matches=keyword_matches,
        ml_confidence=ml_confidence,
        processed_at=datetime.now(timezone.utc),
    )
    session.add(processed)
    session.flush()
    return processed


def bulk_insert_raw(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    inserted = 0
    for row in rows:
        before = get_raw_by_url(session, row["url"])
        create_raw_article(session, row)
        if before is None:
            inserted += 1
    return inserted
