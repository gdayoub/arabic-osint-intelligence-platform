"""Rules-based intelligence briefing generator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.models import ProcessedArticle, RawArticle


def build_intelligence_summary(session: Session, days: int = 7) -> dict:
    """Generate short textual briefing from recent processed records."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    topic_rows = session.execute(
        select(ProcessedArticle.topic, func.count(ProcessedArticle.id))
        .join(RawArticle, RawArticle.id == ProcessedArticle.raw_article_id)
        .where(RawArticle.collected_at >= since)
        .group_by(ProcessedArticle.topic)
        .order_by(func.count(ProcessedArticle.id).desc())
    ).all()

    source_rows = session.execute(
        select(RawArticle.source, func.count(RawArticle.id))
        .where(RawArticle.collected_at >= since)
        .group_by(RawArticle.source)
        .order_by(func.count(RawArticle.id).desc())
    ).all()

    escalation_rows = session.execute(
        select(
            ProcessedArticle.sentiment_or_escalation,
            func.count(ProcessedArticle.id),
        )
        .join(RawArticle, RawArticle.id == ProcessedArticle.raw_article_id)
        .where(RawArticle.collected_at >= since)
        .group_by(ProcessedArticle.sentiment_or_escalation)
        .order_by(func.count(ProcessedArticle.id).desc())
    ).all()

    top_topics = [row[0] for row in topic_rows[:2]]
    top_source = source_rows[0][0] if source_rows else "N/A"
    high_count = next((count for label, count in escalation_rows if label == "high"), 0)

    lines = []
    if top_topics:
        lines.append(
            f"Most coverage in the last {days} days focused on {', '.join(top_topics)} topics."
        )
    if top_source != "N/A":
        lines.append(f"{top_source} published the highest article volume in this window.")
    lines.append(f"High-escalation article count in the window: {high_count}.")

    return {
        "window_days": days,
        "topic_distribution": [{"topic": t, "count": c} for t, c in topic_rows],
        "source_distribution": [{"source": s, "count": c} for s, c in source_rows],
        "escalation_distribution": [
            {"level": e, "count": c} for e, c in escalation_rows
        ],
        "briefing_lines": lines,
    }
