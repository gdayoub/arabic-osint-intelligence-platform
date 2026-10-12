"""Opt-in complete-current metadata archive, with no new runtime activation.

Run explicitly with ``python -m scripts.bake_atlas --out dist/data.json``.
This stages a compatible dashboard, country pages, and countries/_atlas.json.
It does not upload, deploy, fetch source text, or request new translations.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from src.contracts.atlas import build_atlas_snapshot
from src.lang.arabic import ArabicAdapter

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session


@contextmanager
def archive_read_session(engine: Engine) -> Iterator[Session]:
    """Give all bundle queries one read-only database snapshot.

    PostgreSQL READ COMMITTED can mix generations between SELECT statements.
    A fresh REPEATABLE READ transaction fixes that. SQLite's legacy driver
    mode does not start a transaction for SELECT, so BEGIN must be explicit.
    The dedicated connection is always rolled back, never committed.
    """
    from sqlalchemy.orm import Session

    with engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection = connection.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            )
            transaction = connection.begin()
        elif connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
            transaction = connection.get_transaction()
        else:
            raise ValueError("archive snapshots support PostgreSQL and SQLite only")
        try:
            with Session(bind=connection, autoflush=False) as session:
                yield session
        finally:
            transaction.rollback()


def _require_snapshot(session: Session) -> None:
    """Fail rather than silently export a moving PostgreSQL read view."""
    if session.new or session.dirty or session.deleted:
        raise ValueError("archive reader requires a session without pending writes")
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        if connection.get_isolation_level() not in {"REPEATABLE READ", "SERIALIZABLE"}:
            raise ValueError("archive requires a repeatable-read database snapshot")
    elif connection.dialect.name == "sqlite":
        if not connection.connection.driver_connection.in_transaction:
            raise ValueError("archive requires an explicit SQLite read transaction")
    else:
        raise ValueError("archive snapshots support PostgreSQL and SQLite only")


def bake_atlas(session: Session, generated_at: str) -> dict[str, Any]:
    """Read every currently processed live document in the caller's snapshot.

    Reuses the main baker's generation predicates. Legacy entity IDs describe
    this resolver snapshot only. Only current live mentions on included
    articles support membership and surface forms; stored historical aliases
    cannot sneak back into search after their evidence is retracted.
    """
    from sqlalchemy import select

    from scripts.bake_dashboard_data import (
        _current_live_mentions,
        _current_processing,
        _iso_timestamp,
        _latest_fact_values,
    )
    from src.pipeline.process_core import EXTRACTOR_NAME, EXTRACTOR_VERSION
    from src.store.orm import DocumentORM, EntityMentionORM, EntityORM
    from src.store.translations import get_cached

    _require_snapshot(session)
    with session.no_autoflush:
        processing = _current_processing(session)
        titles = _latest_fact_values(session, "title")
        documents = session.scalars(
            select(DocumentORM).where(DocumentORM.retracted.is_(False)).order_by(DocumentORM.id)
        ).all()
        documents = [document for document in documents if document.id in processing.document_ids]
        document_ids = {document.id for document in documents}
        if document_ids != processing.document_ids:
            raise ValueError("current processing membership changed during archive bake")
        mentions = {
            mention.id: mention for mention in _current_live_mentions(session)
            if mention.document_id in document_ids
        }
        entity_rows = session.scalars(
            select(EntityORM).where(EntityORM.retracted.is_(False)).order_by(EntityORM.id)
        ).all()
        entity_by_id = {entity.id: entity for entity in entity_rows}
        entities_by_document: dict[int, set[int]] = defaultdict(set)
        forms_by_entity: dict[int, set[str]] = defaultdict(set)
        entity_by_mention: dict[int, int] = {}
        for membership in session.scalars(select(EntityMentionORM)):
            mention = mentions.get(membership.mention_id)
            entity = entity_by_id.get(membership.entity_id)
            if mention is None or entity is None:
                continue
            if mention.object_type != entity.object_type:
                raise ValueError("archive entity membership has a conflicting object type")
            previous = entity_by_mention.get(mention.id)
            if previous is not None and previous != entity.id:
                raise ValueError("current mention belongs to multiple live entities")
            entity_by_mention[mention.id] = entity.id
            entities_by_document[mention.document_id].add(entity.id)
            forms_by_entity[entity.id].add(mention.text)

        used_entities = set(forms_by_entity)
        text_values = [titles.get(document.id) for document in documents]
        text_values = [title for title in text_values if isinstance(title, str)]
        text_values.extend(entity_by_id[entity_id].canonical_name for entity_id in used_entities)
        # Read the existing cache only. Missing translations remain null.
        translations = get_cached(session, text_values)
        arabic = ArabicAdapter()
        entities = []
        for entity_id in sorted(used_entities):
            entity = entity_by_id[entity_id]
            entities.append({
                "id": str(entity.id),
                "name": entity.canonical_name,
                "name_en": translations.get(entity.canonical_name),
                "object_type": entity.object_type,
                "surface_forms": sorted(forms_by_entity[entity.id]),
                "romanizations": list(arabic.romanize(entity.canonical_name)),
            })

        articles = []
        for document in documents:
            title = titles.get(document.id) or "(untitled)"
            articles.append({
                "id": str(document.id),
                "title": title,
                "title_en": translations.get(title),
                "source": document.source,
                "url": document.url,
                "topic": processing.topics.get(document.id),
                "escalation": processing.escalations.get(document.id),
                "country": processing.countries.get(document.id),
                "published_date": _iso_timestamp(document.published_at),
                "collected_at": _iso_timestamp(document.collected_at),
                "entity_ids": [str(entity_id) for entity_id in sorted(entities_by_document[document.id])],
            })
    return build_atlas_snapshot(
        generated_at=generated_at,
        classification={"name": EXTRACTOR_NAME, "version": EXTRACTOR_VERSION},
        articles=articles,
        entities=entities,
        expected_document_ids={str(document_id) for document_id in processing.document_ids},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Local dashboard bundle output, e.g. dist/data.json")
    args = parser.parse_args()
    from scripts.bake_dashboard_data import run_bake

    data = run_bake(args.out, include_atlas=True)
    print(f"Staged {data['stats']['total_processed']} current processed articles; no deployment performed.")


if __name__ == "__main__":
    main()
