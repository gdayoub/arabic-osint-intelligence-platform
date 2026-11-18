"""Export all current classified article metadata, never private article bodies.

Entity IDs identify this snapshot's resolver generation, not permanent profiles.
The browser stores article bookmarks; entity URLs search by name.
"""
from __future__ import annotations

from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.contracts.atlas import AtlasSnapshot
from src.lang.arabic import ArabicAdapter
from src.pipeline.process_core import EXTRACTOR_NAME, EXTRACTOR_VERSION
from src.store.orm import DocumentORM, EntityORM, EntityMentionORM
from src.store.translations import get_cached


def bake_atlas(session: Session, generated_at: str) -> dict:
    # Imported here to keep the existing baker's public entry points compatible.
    from scripts.bake_dashboard_data import _current_processing, _current_live_mentions, _latest_fact_values, _iso_timestamp

    processing = _current_processing(session)
    titles = _latest_fact_values(session, "title")
    documents = session.scalars(select(DocumentORM).where(DocumentORM.retracted.is_(False)).order_by(DocumentORM.id)).all()
    documents = [document for document in documents if document.id in processing.document_ids]
    document_ids = {document.id for document in documents}
    mentions = {mention.id: mention for mention in _current_live_mentions(session) if mention.document_id in document_ids}
    entity_rows = session.scalars(select(EntityORM).where(EntityORM.retracted.is_(False)).order_by(EntityORM.id)).all()
    entity_by_id = {entity.id: entity for entity in entity_rows}
    entities_by_document: dict[int, set[int]] = defaultdict(set)
    used_entities: set[int] = set()
    for membership in session.scalars(select(EntityMentionORM)):
        mention = mentions.get(membership.mention_id)
        if mention is not None and membership.entity_id in entity_by_id:
            entities_by_document[mention.document_id].add(membership.entity_id)
            used_entities.add(membership.entity_id)

    text_values = [title for title in titles.values() if isinstance(title, str)]
    text_values.extend(entity_by_id[entity_id].canonical_name for entity_id in used_entities)
    translations = get_cached(session, text_values)
    arabic = ArabicAdapter()
    entities = []
    for entity_id in sorted(used_entities):
        entity = entity_by_id[entity_id]
        entities.append({
            "id": str(entity.id), "name": entity.canonical_name,
            "name_en": translations.get(entity.canonical_name), "object_type": entity.object_type,
            "surface_forms": sorted(set((entity.properties or {}).get("surface_forms") or [entity.canonical_name])),
            "romanizations": list(arabic.romanize(entity.canonical_name)),
        })

    articles = []
    for document in documents:
        title = titles.get(document.id) or "(untitled)"
        articles.append({
            "id": str(document.id), "title": title, "title_en": translations.get(title),
            "source": document.source, "url": document.url,
            "topic": processing.topics.get(document.id), "escalation": processing.escalations.get(document.id),
            "country": processing.countries.get(document.id),
            "published_date": _iso_timestamp(document.published_at), "collected_at": _iso_timestamp(document.collected_at),
            "entity_ids": [str(entity_id) for entity_id in sorted(entities_by_document[document.id])],
        })
    payload = {
        "atlas_version": 1, "generated_at": generated_at, "complete": True,
        "classification": {"name": EXTRACTOR_NAME, "version": EXTRACTOR_VERSION},
        "articles": articles, "entities": entities,
    }
    return AtlasSnapshot.model_validate(payload).model_dump(mode="json")
