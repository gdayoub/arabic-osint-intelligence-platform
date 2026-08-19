"""Bakes a static data.json snapshot of the core schema for the Cloudflare
Pages dashboard (M1.5 Stage 6).

Matches the response shape of the retiring /api/stats, /api/topics,
/api/escalation, /api/recent endpoints (src/api/main.py) so
src/api/static/dashboard.html needs only a small fetch change, not a
rewrite. Run after ingest-core + process-core, e.g. from CI:

    python main.py bake-dashboard --out dist/data.json

Never includes document body text — the output is written to a public
Pages deployment. Titles, URLs, and aggregate counts only; anything that
needs the source text goes through `provenance show`, not this file.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.store.database import get_core_session
from src.store.orm import DocumentORM, FactORM
from src.store.translations import get_cached

RECENT_LIMIT_DEFAULT = 30
DAILY_WINDOW_DAYS = 30


def _latest_fact_values(session: Session, fact_type: str) -> dict[int, Any]:
    """Most recent fact value per document for a given fact_type.

    Facts are append-only (P5) — a document can accumulate more than one
    fact of the same type over time (a re-classification, a corrected
    title). Iterating oldest-to-newest and overwriting the dict as we go
    means the last write for each document id wins in one pass, without
    needing to walk supersedes_id chains explicitly (nothing currently
    writes a fact out of chronological order, so created_at ordering and
    the supersede chain agree in practice).
    """
    rows = session.scalars(
        select(FactORM)
        .where(
            FactORM.subject_table == "documents",
            FactORM.fact_type == fact_type,
            FactORM.retracted.is_(False),
        )
        .order_by(FactORM.created_at.asc())
    )
    latest: dict[int, Any] = {}
    for row in rows:
        latest[row.subject_id] = row.payload.get("value")
    return latest


def _daily_counts(session: Session, days: int = DAILY_WINDOW_DAYS) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(func.date(DocumentORM.collected_at).label("day"), func.count().label("count"))
        .where(DocumentORM.collected_at >= cutoff, DocumentORM.retracted.is_(False))
        .group_by("day")
        .order_by("day")
    ).all()
    return [{"date": str(row.day), "count": row.count} for row in rows]


def bake(session: Session, recent_limit: int = RECENT_LIMIT_DEFAULT) -> dict[str, Any]:
    total_raw = (
        session.scalar(select(func.count()).select_from(DocumentORM).where(DocumentORM.retracted.is_(False))) or 0
    )

    title_by_doc = _latest_fact_values(session, "title")
    topic_by_doc = _latest_fact_values(session, "topic")
    escalation_by_doc = _latest_fact_values(session, "escalation")
    country_by_doc = _latest_fact_values(session, "country")

    total_processed = len(topic_by_doc)  # a document counts as processed once it has a topic fact

    source_rows = session.execute(
        select(DocumentORM.source, func.count().label("count"))
        .where(DocumentORM.retracted.is_(False))
        .group_by(DocumentORM.source)
        .order_by(func.count().desc())
    ).all()

    topic_counts = Counter(v for v in topic_by_doc.values() if v is not None)
    escalation_counts = Counter(v for v in escalation_by_doc.values() if v is not None)

    recent_rows = session.scalars(
        select(DocumentORM)
        .where(DocumentORM.retracted.is_(False))
        .order_by(DocumentORM.collected_at.desc())
        .limit(recent_limit)
    ).all()

    # One lookup for every title on the page rather than per-row queries.
    # Missing translations are expected and fine — the UI shows Arabic
    # regardless and only offers a toggle when an English version exists.
    recent_titles = [title_by_doc.get(doc.id) for doc in recent_rows]
    translations = get_cached(session, [t for t in recent_titles if t])

    recent = [
        {
            "title": title_by_doc.get(doc.id) or "(untitled)",
            "title_en": translations.get(title_by_doc.get(doc.id) or ""),
            "source": doc.source,
            "url": doc.url,
            "topic": topic_by_doc.get(doc.id),
            "escalation": escalation_by_doc.get(doc.id),
            "country": country_by_doc.get(doc.id),
            "ai_summary": None,  # not produced by process_core.py — see docs/adr/0011
            "processed_at": doc.collected_at.isoformat() if doc.collected_at else None,
            "published_date": doc.published_at.isoformat() if doc.published_at else None,
        }
        for doc in recent_rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "stats": {
            "total_raw": total_raw,
            "total_processed": total_processed,
            "sources": {row.source: row.count for row in source_rows},
        },
        "topics": {"topics": [{"topic": topic, "count": count} for topic, count in topic_counts.most_common()]},
        "escalation": {"escalation": dict(escalation_counts)},
        "recent": recent,
        "daily": _daily_counts(session),
    }


def write_data_json(data: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False: same reasoning as the JSON column fix in
    # src/store/database.py -- escaping Arabic to \uXXXX roughly triples it.
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_bake(out_path: Path, recent_limit: int = RECENT_LIMIT_DEFAULT) -> dict[str, Any]:
    with get_core_session() as session:
        data = bake(session, recent_limit=recent_limit)
    write_data_json(data, out_path)
    return data
