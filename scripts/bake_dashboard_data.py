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
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.contracts.dashboard import SCHEMA_VERSION, validate_snapshot_bundle
from src.store.database import get_core_session
from src.pipeline.extract_core import EXTRACTION_MARKER
from src.pipeline.process_core import EXTRACTOR_NAME as PROCESSOR_NAME
from src.pipeline.process_core import EXTRACTOR_VERSION as PROCESSOR_VERSION
from src.resolve.review import latest_decisions
from src.store.orm import (
    DocumentORM,
    EntityMentionORM,
    EntityORM,
    ExtractorVersionORM,
    FactORM,
    MentionORM,
    ReviewPairORM,
)
from src.lang.arabic import ArabicAdapter
from src.store.translations import get_cached

RECENT_LIMIT_DEFAULT = 30
DAILY_WINDOW_DAYS = 30
COUNTRY_ARTICLE_LIMIT = 50
REVIEW_QUEUE_LIMIT = 20
_REQUIRED_PROCESSING_FACT_TYPES = ("topic", "escalation")


@dataclass(slots=True)
class CurrentProcessing:
    """The completed document-classification generation used by the bake.

    ``topic`` is process_core's completion marker, but a complete public row
    also needs ``escalation`` because that fact is always produced by the
    same run. ``country`` is optional, so its absence does not make a
    document incomplete.
    """

    document_ids: set[int]
    topics: dict[int, Any]
    escalations: dict[int, Any]
    countries: dict[int, Any]


def _latest_fact_rows(session: Session, fact_type: str) -> dict[int, FactORM]:
    """Newest live fact row per live document for one fact type."""
    rows = session.scalars(
        select(FactORM)
        .join(
            DocumentORM,
            (FactORM.subject_table == "documents")
            & (DocumentORM.id == FactORM.subject_id),
        )
        .where(
            FactORM.fact_type == fact_type,
            FactORM.retracted.is_(False),
            DocumentORM.retracted.is_(False),
        )
        .order_by(FactORM.created_at.asc(), FactORM.id.asc())
    )

    latest: dict[int, FactORM] = {}
    for row in rows:
        latest[row.subject_id] = row
    return latest


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
    return {
        document_id: row.payload.get("value")
        for document_id, row in _latest_fact_rows(session, fact_type).items()
    }


def _current_processing(session: Session) -> CurrentProcessing:
    """Return only live documents completed by the configured processor.

    Facts are append-only, so merely finding any current-version topic row is
    not enough: a newer stale fact may have superseded it. We first select the
    latest live row for every required type, then require both latest rows to
    belong to the configured extractor version. This makes the completed IDs
    a true subset of live documents and prevents mixed processing generations
    from entering public aggregates.

    The schema does not yet identify individual attempts within one extractor
    version. Per-attempt atomicity belongs to F0.1b; this checkpoint uses the
    strongest generation boundary the current schema can represent.
    """
    extractor_version_id = session.scalar(
        select(ExtractorVersionORM.id).where(
            ExtractorVersionORM.name == PROCESSOR_NAME,
            ExtractorVersionORM.version == PROCESSOR_VERSION,
        )
    )
    if extractor_version_id is None:
        return CurrentProcessing(set(), {}, {}, {})

    latest_by_type = {
        fact_type: _latest_fact_rows(session, fact_type)
        for fact_type in (*_REQUIRED_PROCESSING_FACT_TYPES, "country")
    }
    topic_rows = latest_by_type["topic"]
    escalation_rows = latest_by_type["escalation"]

    completed_document_ids = set(topic_rows) & set(escalation_rows)
    completed_document_ids = {
        document_id
        for document_id in completed_document_ids
        if topic_rows[document_id].extractor_version_id == extractor_version_id
        and escalation_rows[document_id].extractor_version_id == extractor_version_id
    }

    topics = {
        document_id: topic_rows[document_id].payload.get("value")
        for document_id in completed_document_ids
    }
    escalations = {
        document_id: escalation_rows[document_id].payload.get("value")
        for document_id in completed_document_ids
    }
    country_rows = latest_by_type["country"]
    countries = {
        document_id: country_rows[document_id].payload.get("value")
        for document_id in completed_document_ids
        if document_id in country_rows
        and country_rows[document_id].extractor_version_id == extractor_version_id
    }
    return CurrentProcessing(completed_document_ids, topics, escalations, countries)


def _current_live_mentions(session: Session) -> list[MentionORM]:
    """Live mentions from each completed extraction-family generation.

    The extraction marker is written last and carries the same extractor
    version ID as its mentions. A document can intentionally have several
    extractor families, so the newest marker is selected per document and
    extractor name. Older versions within each family remain append-only
    history, but they must not inflate the current public snapshot.
    """
    marker_rows = session.execute(
        select(FactORM, ExtractorVersionORM.name)
        .join(
            DocumentORM,
            (FactORM.subject_table == "documents")
            & (DocumentORM.id == FactORM.subject_id),
        )
        .join(
            ExtractorVersionORM,
            ExtractorVersionORM.id == FactORM.extractor_version_id,
        )
        .where(
            FactORM.fact_type == EXTRACTION_MARKER,
            FactORM.retracted.is_(False),
            DocumentORM.retracted.is_(False),
        )
        .order_by(FactORM.created_at.asc(), FactORM.id.asc())
    ).all()

    latest_marker_version: dict[tuple[int, str], int] = {}
    for marker, extractor_name in marker_rows:
        latest_marker_version[(marker.subject_id, extractor_name)] = (
            marker.extractor_version_id
        )
    if not latest_marker_version:
        return []

    mention_rows = session.execute(
        select(MentionORM, ExtractorVersionORM.name)
        .join(DocumentORM, DocumentORM.id == MentionORM.document_id)
        .join(
            ExtractorVersionORM,
            ExtractorVersionORM.id == MentionORM.extractor_version_id,
        )
        .where(MentionORM.retracted.is_(False), DocumentORM.retracted.is_(False))
    ).all()
    return [
        mention
        for mention, extractor_name in mention_rows
        if mention.extractor_version_id
        == latest_marker_version.get((mention.document_id, extractor_name))
    ]


def _daily_counts(session: Session, days: int = DAILY_WINDOW_DAYS) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(func.date(DocumentORM.collected_at).label("day"), func.count().label("count"))
        .where(DocumentORM.collected_at >= cutoff, DocumentORM.retracted.is_(False))
        .group_by("day")
        .order_by("day")
    ).all()
    return [{"date": str(row.day), "count": row.count} for row in rows]


def country_slug(country: str) -> str:
    """URL-safe filename for a country. 'Saudi Arabia' -> 'saudi-arabia'."""
    return re.sub(r"[^a-z0-9]+", "-", country.lower()).strip("-")


def bake_country_pages(
    session: Session,
    per_country_limit: int = COUNTRY_ARTICLE_LIMIT,
    generated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """One JSON payload per country, for the country drill-down pages.

    Emitted as separate files rather than nested inside data.json so the
    main dashboard payload stays small — a reader looking at Syria
    shouldn't download every other country's articles to see it.
    """
    title_by_doc = _latest_fact_values(session, "title")
    processing = _current_processing(session)
    topic_by_doc = processing.topics
    escalation_by_doc = processing.escalations
    country_by_doc = processing.countries

    docs_by_country: dict[str, list[int]] = defaultdict(list)
    for document_id, country in country_by_doc.items():
        if isinstance(country, str) and country:
            docs_by_country[country].append(document_id)

    if not docs_by_country:
        return {}

    all_ids = [doc_id for ids in docs_by_country.values() for doc_id in ids]
    rows = session.scalars(
        select(DocumentORM).where(DocumentORM.id.in_(all_ids), DocumentORM.retracted.is_(False))
    ).all()
    doc_by_id = {row.id: row for row in rows}

    translations = get_cached(session, [t for t in title_by_doc.values() if isinstance(t, str)])

    snapshot_time = generated_at or datetime.now(timezone.utc).isoformat()
    pages: dict[str, dict[str, Any]] = {}
    for country, document_ids in docs_by_country.items():
        present = [doc_by_id[i] for i in document_ids if i in doc_by_id]
        if not present:
            continue
        present.sort(key=lambda d: _as_utc(d.collected_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        topic_counts = Counter(
            topic_by_doc[d.id] for d in present if topic_by_doc.get(d.id) is not None
        )
        escalation_counts = Counter(
            escalation_by_doc[d.id] for d in present if escalation_by_doc.get(d.id) is not None
        )
        source_counts = Counter(d.source for d in present)

        pages[country] = {
            "generated_at": snapshot_time,
            "schema_version": SCHEMA_VERSION,
            "country": country,
            "slug": country_slug(country),
            "total": len(present),
            "sources": dict(source_counts.most_common()),
            "topics": [{"topic": t, "count": c} for t, c in topic_counts.most_common()],
            "escalation": dict(escalation_counts),
            "daily": _daily_counts_for(present),
            "articles": [
                {
                    "title": title_by_doc.get(d.id) or "(untitled)",
                    "title_en": translations.get(title_by_doc.get(d.id) or ""),
                    "source": d.source,
                    "url": d.url,
                    "topic": topic_by_doc.get(d.id),
                    "escalation": escalation_by_doc.get(d.id),
                    "published_date": _iso_timestamp(d.published_at),
                    "processed_at": _iso_timestamp(d.collected_at),
                }
                for d in present[:per_country_limit]
            ],
        }
    return pages


def _as_utc(value: datetime | None) -> datetime | None:
    """Force a datetime to be timezone-aware UTC.

    Postgres returns aware datetimes for DateTime(timezone=True); SQLite —
    which the unit tests run on — returns naive ones for the same column.
    Comparing the two raises TypeError, so every comparison against a
    tz-aware "now" goes through here rather than trusting the driver.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso_timestamp(value: datetime | None) -> str | None:
    """Serialize a database datetime with an explicit UTC offset.

    SQLite drops timezone metadata even for timezone-aware SQLAlchemy columns.
    These columns are UTC, so local/test values get that lost marker restored
    before they cross the strict public contract.
    """
    utc_value = _as_utc(value)
    return utc_value.isoformat() if utc_value is not None else None


def _daily_counts_for(documents: list[DocumentORM], days: int = DAILY_WINDOW_DAYS) -> list[dict[str, Any]]:
    """Per-day counts for an already-fetched set of documents.

    Counted in Python rather than SQL because the caller already holds every
    row it needs — issuing one grouped query per country would be dozens of
    round trips to recompute what's already in memory.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    counts: Counter[str] = Counter()
    for document in documents:
        collected = _as_utc(document.collected_at)
        if collected and collected >= cutoff:
            counts[collected.date().isoformat()] += 1
    return [{"date": day, "count": counts[day]} for day in sorted(counts)]


TOP_MENTIONS_PER_TYPE = 12


def top_mentions(
    session: Session,
    per_type: int = TOP_MENTIONS_PER_TYPE,
    mentions: list[MentionORM] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """most mentioned things per object type.

    I group by the NORMALIZED surface form so بشار الأسد and بشار الاسد count
    as one row. that is the M2 folding earning its keep. I still display the
    most common raw spelling because showing the reader a folded string would
    be showing them something nobody wrote.

    this is not entity resolution. الأسد and بشار الأسد are still two rows
    here because nothing has decided they are the same thing yet. M4 is what
    collapses those and the difference should be visible on this panel when
    it lands.
    """
    adapter = ArabicAdapter()
    mentions = mentions if mentions is not None else _current_live_mentions(session)

    # normalized key -> counts, plus a tally of raw spellings so I can show
    # whichever one people actually write most
    grouped: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for mention in mentions:
        key = adapter.normalize(mention.text)
        if key:
            grouped[(mention.object_type, key)][mention.text] += 1

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (object_type, _key), spellings in grouped.items():
        display, _ = spellings.most_common(1)[0]
        by_type[object_type].append({"name": display, "count": sum(spellings.values())})

    return {
        object_type: sorted(items, key=lambda e: e["count"], reverse=True)[:per_type]
        for object_type, items in by_type.items()
    }


def top_entities(
    session: Session,
    per_type: int = TOP_MENTIONS_PER_TYPE,
    current_mention_ids: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """most mentioned RESOLVED entities per type.

    the difference from top_mentions is the whole point of M4. that one
    counts surface strings so ترامب and دونالد ترامب are two rows. this one
    counts entities so they are one row with both spellings listed under it.

    surface_forms comes along because a reader should be able to see WHY two
    rows became one. an entity that silently swallowed four spellings is
    much harder to trust than one that shows its working.
    """
    if current_mention_ids is None:
        current_mention_ids = {mention.id for mention in _current_live_mentions(session)}
    if not current_mention_ids:
        return {}

    rows = session.execute(
        select(
            EntityORM.id,
            EntityORM.canonical_name,
            EntityORM.object_type,
            EntityORM.properties,
            func.count(EntityMentionORM.mention_id).label("mentions"),
        )
        .join(EntityMentionORM, EntityMentionORM.entity_id == EntityORM.id)
        .where(
            EntityORM.retracted.is_(False),
            EntityMentionORM.mention_id.in_(current_mention_ids),
        )
        .group_by(EntityORM.id, EntityORM.canonical_name, EntityORM.object_type, EntityORM.properties)
    ).all()

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _eid, name, object_type, properties, mentions in rows:
        forms = (properties or {}).get("surface_forms") or []
        by_type[object_type].append(
            {
                "name": name,
                "count": mentions,
                # only worth showing when resolution actually merged
                # something. one form means nothing was joined.
                "surface_forms": forms if len(forms) > 1 else [],
            }
        )

    return {
        object_type: sorted(items, key=lambda e: e["count"], reverse=True)[:per_type]
        for object_type, items in by_type.items()
    }


def pending_review_pairs(
    session: Session,
    limit: int = REVIEW_QUEUE_LIMIT,
    current_mention_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Public evidence for unresolved entity pairs, nearest threshold first.

    The static dashboard cannot read document bodies because ``data.json`` is
    public.  A source title, URL, publisher, and the exact stored mention are
    enough to let a reviewer open the original evidence without crossing that
    boundary.  Decisions are append-only, so a pair disappears from this view
    whenever its latest human decision exists.
    """
    decided = set(latest_decisions(session))
    rows = session.scalars(
        select(ReviewPairORM).order_by(ReviewPairORM.id.desc())
    ).all()

    # A pair can be rescored by a later model version. Show only its newest
    # snapshot, not one card per historical scorer.
    newest: dict[tuple[int, int], ReviewPairORM] = {}
    for row in rows:
        key = (row.left_mention_id, row.right_mention_id)
        if key not in decided and key not in newest:
            newest[key] = row

    pending = sorted(
        newest.values(), key=lambda row: (abs(row.score - row.threshold), -row.id)
    )[:limit]
    if not pending:
        return []

    if current_mention_ids is None:
        current_mention_ids = {mention.id for mention in _current_live_mentions(session)}

    mention_ids = {
        mention_id
        for row in pending
        for mention_id in (row.left_mention_id, row.right_mention_id)
        if mention_id in current_mention_ids
    }
    mentions = session.scalars(
        select(MentionORM).where(
            MentionORM.id.in_(mention_ids), MentionORM.retracted.is_(False)
        )
    ).all()
    mention_by_id = {mention.id: mention for mention in mentions}

    document_ids = {mention.document_id for mention in mentions}
    documents = session.scalars(
        select(DocumentORM).where(
            DocumentORM.id.in_(document_ids), DocumentORM.retracted.is_(False)
        )
    ).all()
    document_by_id = {document.id: document for document in documents}
    title_by_doc = _latest_fact_values(session, "title")

    def evidence(mention_id: int) -> dict[str, Any] | None:
        mention = mention_by_id.get(mention_id)
        document = document_by_id.get(mention.document_id) if mention else None
        if mention is None or document is None:
            return None
        return {
            "mention_id": mention.id,
            "text": mention.text,
            "source": document.source,
            "url": document.url,
            "title": title_by_doc.get(document.id) or "(untitled)",
        }

    items: list[dict[str, Any]] = []
    for row in pending:
        left = evidence(row.left_mention_id)
        right = evidence(row.right_mention_id)
        if left is None or right is None:
            continue
        items.append(
            {
                "id": row.id,
                "object_type": row.object_type,
                "score": row.score,
                "threshold": row.threshold,
                "distance": abs(row.score - row.threshold),
                "features": row.features,
                "left": left,
                "right": right,
            }
        )
    return items


def bake(
    session: Session,
    recent_limit: int = RECENT_LIMIT_DEFAULT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    total_raw = (
        session.scalar(select(func.count()).select_from(DocumentORM).where(DocumentORM.retracted.is_(False))) or 0
    )

    title_by_doc = _latest_fact_values(session, "title")
    processing = _current_processing(session)
    topic_by_doc = processing.topics
    escalation_by_doc = processing.escalations
    country_by_doc = processing.countries
    total_processed = len(processing.document_ids)
    if total_processed > total_raw:
        raise ValueError(
            "public snapshot invariant failed: total_processed cannot exceed total_raw"
        )

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
            "processed_at": _iso_timestamp(doc.collected_at),
            "published_date": _iso_timestamp(doc.published_at),
        }
        for doc in recent_rows
    ]

    current_mentions = _current_live_mentions(session)
    current_mention_ids = {mention.id for mention in current_mentions}
    current_entity_total = session.scalar(
        select(func.count(func.distinct(EntityMentionORM.entity_id)))
        .join(EntityORM, EntityORM.id == EntityMentionORM.entity_id)
        .where(
            EntityORM.retracted.is_(False),
            EntityMentionORM.mention_id.in_(current_mention_ids),
        )
    ) if current_mention_ids else 0

    return {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "stats": {
            "total_raw": total_raw,
            "total_processed": total_processed,
            "sources": {row.source: row.count for row in source_rows},
        },
        "topics": {"topics": [{"topic": topic, "count": count} for topic, count in topic_counts.most_common()]},
        "escalation": {"escalation": dict(escalation_counts)},
        "recent": recent,
        "daily": _daily_counts(session),
        "mentions": {
            "total": len(current_mentions),
            "top": top_mentions(session, mentions=current_mentions),
        },
        "entities": {
            "total": current_entity_total or 0,
            "top": top_entities(session, current_mention_ids=current_mention_ids),
        },
        "review_queue": {
            "items": pending_review_pairs(
                session, current_mention_ids=current_mention_ids
            ),
        },
    }


def write_data_json(data: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False: same reasoning as the JSON column fix in
    # src/store/database.py -- escaping Arabic to \uXXXX roughly triples it.
    rendered = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    out_path.write_text(rendered, encoding="utf-8")


def _remove_exact_path(path: Path) -> None:
    """Remove one known publication target while rolling back a failed swap."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _publish_staged_snapshot(
    data: dict[str, Any],
    country_pages: dict[str, dict[str, Any]],
    out_path: Path,
) -> None:
    """Stage every JSON file, then make the main snapshot the commit marker.

    Validation happens before this function. A staging write cannot touch the
    live files. Promotion swaps the complete country directory first and the
    main file last; any failed swap restores the exact prior targets.
    """
    out_path = out_path.resolve()
    parent = out_path.parent
    if parent == Path(parent.anchor):
        raise ValueError(
            "refusing to publish a snapshot directly under a filesystem root"
        )
    if out_path.name in {"", ".", "..", "countries"}:
        raise ValueError(
            "snapshot output must be a JSON filename outside countries/"
        )

    parent.mkdir(parents=True, exist_ok=True)
    countries_path = parent / "countries"

    with tempfile.TemporaryDirectory(
        dir=parent, prefix=".dashboard-snapshot-stage-"
    ) as stage_name, tempfile.TemporaryDirectory(
        dir=parent, prefix=".dashboard-snapshot-backup-"
    ) as backup_name:
        stage = Path(stage_name)
        backup = Path(backup_name)
        staged_data = stage / out_path.name
        staged_countries = stage / "countries"
        staged_countries.mkdir()

        write_data_json(data, staged_data)
        for page in country_pages.values():
            write_data_json(page, staged_countries / f"{page['slug']}.json")

        had_data = out_path.exists()
        had_countries = countries_path.exists()
        backup_data = backup / out_path.name
        backup_countries = backup / "countries"
        if had_data:
            shutil.copy2(out_path, backup_data)

        country_promoted = False
        data_promoted = False
        try:
            if had_countries:
                os.replace(countries_path, backup_countries)
            os.replace(staged_countries, countries_path)
            country_promoted = True

            # Existing readers never see a new index before its country files.
            os.replace(staged_data, out_path)
            data_promoted = True
        except Exception:
            if data_promoted:
                if had_data:
                    os.replace(backup_data, out_path)
                else:
                    out_path.unlink(missing_ok=True)

            if country_promoted:
                _remove_exact_path(countries_path)
            if had_countries and backup_countries.exists():
                os.replace(backup_countries, countries_path)
            raise


def publish_snapshot_bundle(
    data: dict[str, Any],
    country_pages: dict[str, dict[str, Any]],
    out_path: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate and publish one dashboard/country snapshot candidate."""
    validated_data, validated_pages = validate_snapshot_bundle(
        data, country_pages
    )
    _publish_staged_snapshot(validated_data, validated_pages, out_path)
    return validated_data, validated_pages


def run_bake(out_path: Path, recent_limit: int = RECENT_LIMIT_DEFAULT) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    with get_core_session() as session:
        data = bake(
            session,
            recent_limit=recent_limit,
            generated_at=generated_at,
        )
        country_pages = bake_country_pages(session, generated_at=generated_at)

    # The index the main dashboard links from: name, slug, count. The heavy
    # per-country payloads stay in their own files.
    data["countries"] = [
        {"country": page["country"], "slug": page["slug"], "count": page["total"]}
        for page in sorted(country_pages.values(), key=lambda p: p["total"], reverse=True)
    ]

    data, country_pages = publish_snapshot_bundle(data, country_pages, out_path)

    return data
