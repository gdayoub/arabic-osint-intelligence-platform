"""The sanctioned write path for provenance-bearing facts (P1).

Nothing outside this module inserts into mentions, entities, links, facts, or
provenance directly. Every function here that creates a fact-bearing row also
writes its provenance row in the same session, before the caller commits — so
a mention, entity, or link can only come into existence with its lineage
attached. There is no second door.

This is enforced by convention + code review, not by a database trigger. See
docs/adr/0002-provenance-enforcement.md for why, and what would change that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import Document, Entity, ExtractorVersion, Fact, Link, Mention
from src.core.ontology import Ontology
from src.store.blob import BlobStore, compress_text, sha256_text, text_blob_key
from src.store.documents import resolve_document_text
from src.store.orm import (
    DocumentORM,
    EntityMentionORM,
    EntityORM,
    ExtractorVersionORM,
    FactORM,
    LinkORM,
    MentionORM,
    ProvenanceORM,
)


def register_extractor_version(
    session: Session, name: str, version: str, description: str | None = None
) -> ExtractorVersion:
    """Get-or-create so re-running a pipeline doesn't fail on the unique constraint."""
    existing = session.scalar(
        select(ExtractorVersionORM).where(
            ExtractorVersionORM.name == name, ExtractorVersionORM.version == version
        )
    )
    if existing is not None:
        return _to_extractor_version(existing)

    row = ExtractorVersionORM(name=name, version=version, description=description)
    session.add(row)
    session.flush()  # assigns row.id without committing the transaction
    return _to_extractor_version(row)


def create_document(
    session: Session,
    source: str,
    text: str,
    content_hash: str,
    blob_store: BlobStore,
    url: str | None = None,
    published_at: datetime | None = None,
    collected_at: datetime | None = None,
) -> Document:
    """Write the text to blob storage, then the row that points at it.

    That order matters: if the process dies between the two writes, the
    result is an orphaned blob (harmless — content-addressed, costs a few
    KB, a retry with the same text is a no-op) rather than a Postgres row
    pointing at text that was never written (a P1 violation — a provenance
    chain that can't reach real source text). Always fail toward garbage,
    never toward a dangling reference.
    """
    sha256 = sha256_text(text)
    key = text_blob_key(sha256)
    if not blob_store.exists(key):
        blob_store.put(
            key,
            compress_text(text),
            content_type="application/gzip",
        )

    row = DocumentORM(
        source=source,
        text=None,
        text_blob_key=key,
        text_sha256=sha256,
        text_length=len(text),
        content_hash=content_hash,
        url=url,
        published_at=published_at,
    )
    if collected_at is not None:
        row.collected_at = collected_at  # else leave unset so the column default (now()) applies
    session.add(row)
    session.flush()
    return Document(
        id=row.id,
        source=row.source,
        text=text,
        content_hash=row.content_hash,
        url=row.url,
        published_at=row.published_at,
        collected_at=row.collected_at,
        retracted=row.retracted,
        retracted_reason=row.retracted_reason,
    )


def create_mention(
    session: Session,
    document: Document,
    text: str,
    start: int,
    end: int,
    object_type: str,
    extractor_version: ExtractorVersion,
    ontology: Ontology,
) -> Mention:
    """Create a mention and its provenance row together.

    Raises ValueError if the offsets don't match the document text (P2) or
    object_type isn't declared in ontology.yaml (P3).
    """
    if document.text[start:end] != text:
        raise ValueError(
            f"Offset mismatch: document.text[{start}:{end}] = "
            f"{document.text[start:end]!r}, but mention text is {text!r}"
        )
    if not ontology.is_valid_object_type(object_type):
        raise ValueError(f"object_type {object_type!r} is not declared in ontology.yaml")

    mention_row = MentionORM(
        document_id=document.id,
        text=text,
        start_offset=start,
        end_offset=end,
        object_type=object_type,
        extractor_version_id=extractor_version.id,
    )
    session.add(mention_row)
    session.flush()  # need mention_row.id for the provenance row below

    session.add(
        ProvenanceORM(
            target_table="mentions",
            target_id=mention_row.id,
            document_id=document.id,
            mention_id=mention_row.id,
            extractor_version_id=extractor_version.id,
        )
    )
    return _to_mention(mention_row)


def create_entity(
    session: Session,
    object_type: str,
    canonical_name: str,
    properties: dict,
    source_mention: Mention,
    extractor_version: ExtractorVersion,
    ontology: Ontology,
) -> Entity:
    """Create an entity evidenced by one mention, with provenance pointing at
    the same document/mention/extractor that justified it.
    """
    if not ontology.is_valid_object_type(object_type):
        raise ValueError(f"object_type {object_type!r} is not declared in ontology.yaml")

    entity_row = EntityORM(object_type=object_type, canonical_name=canonical_name, properties=properties)
    session.add(entity_row)
    session.flush()

    session.add(EntityMentionORM(entity_id=entity_row.id, mention_id=source_mention.id))
    session.add(
        ProvenanceORM(
            target_table="entities",
            target_id=entity_row.id,
            document_id=source_mention.document_id,
            mention_id=source_mention.id,
            extractor_version_id=extractor_version.id,
        )
    )
    return _to_entity(entity_row)


def add_entity_evidence(
    session: Session,
    entity: Entity,
    mention: Mention,
    extractor_version: ExtractorVersion,
) -> None:
    """attach one more mention to an existing entity as evidence.

    create_entity takes a single founding mention. an entity resolved from a
    cluster has many, and every one of them is a separate piece of evidence
    that needs its own provenance row. without this the entity would claim
    forty mentions in entity_mentions while provenance only justified one,
    and "why does the system believe this" would have a much thinner answer
    than the data suggests.

    idempotent on re-run because entity_mentions has a composite primary key
    and I check before inserting rather than letting it raise.
    """
    existing = session.get(EntityMentionORM, (entity.id, mention.id))
    if existing is not None:
        return

    session.add(EntityMentionORM(entity_id=entity.id, mention_id=mention.id))
    session.add(
        ProvenanceORM(
            target_table="entities",
            target_id=entity.id,
            document_id=mention.document_id,
            mention_id=mention.id,
            extractor_version_id=extractor_version.id,
        )
    )


def link_entities(
    session: Session,
    link_type: str,
    from_entity: Entity,
    to_entity: Entity,
    document: Document,
    extractor_version: ExtractorVersion,
    ontology: Ontology,
    confidence: float | None = None,
    source_mention: Mention | None = None,
) -> Link:
    if not ontology.is_valid_link_type(link_type, from_entity.object_type, to_entity.object_type):
        raise ValueError(
            f"link_type {link_type!r} is not valid between "
            f"{from_entity.object_type!r} and {to_entity.object_type!r}"
        )

    link_row = LinkORM(
        link_type=link_type,
        from_entity_id=from_entity.id,
        to_entity_id=to_entity.id,
        confidence=confidence,
        extractor_version_id=extractor_version.id,
    )
    session.add(link_row)
    session.flush()

    session.add(
        ProvenanceORM(
            target_table="links",
            target_id=link_row.id,
            document_id=document.id,
            mention_id=source_mention.id if source_mention else None,
            extractor_version_id=extractor_version.id,
        )
    )
    return _to_link(link_row)


def record_document_fact(
    session: Session,
    document: Document,
    fact_type: str,
    value: Any,
    extractor_version: ExtractorVersion,
    ontology: Ontology,
    supersedes: Fact | None = None,
) -> Fact:
    """The sanctioned path for document-level metadata that isn't a core
    column — title, author, tags, a topic label, an escalation score. These
    are news-specific (P3: documents.* must stay domain-agnostic), so they
    live as facts instead of columns. See docs/adr/0007-source-metadata-as-facts.md.

    supersedes replaces a prior fact of the same type without mutating it
    (P5) — pass the old Fact and it's linked via supersedes_id, never edited.
    """
    if not ontology.is_valid_document_attribute(fact_type):
        raise ValueError(f"fact_type {fact_type!r} is not declared in ontology.yaml's document_attributes")

    fact_row = FactORM(
        fact_type=fact_type,
        subject_table="documents",
        subject_id=document.id,
        payload={"value": value},
        extractor_version_id=extractor_version.id,
        supersedes_id=supersedes.id if supersedes else None,
    )
    session.add(fact_row)
    session.flush()

    session.add(
        ProvenanceORM(
            target_table="facts",
            target_id=fact_row.id,
            document_id=document.id,
            mention_id=None,
            extractor_version_id=extractor_version.id,
        )
    )
    return _to_fact(fact_row)


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """One hop in a provenance chain, already joined against its document."""

    target_table: str
    target_id: int
    document_id: int
    document_source: str
    document_text: str
    mention_text: str | None
    mention_start: int | None
    mention_end: int | None
    extractor_name: str
    extractor_version: str
    created_at: datetime


def get_provenance_chain(
    session: Session, target_table: str, target_id: int, blob_store: BlobStore
) -> list[ProvenanceEntry]:
    """Every provenance row recorded for a given entity/link/mention/fact,
    each already resolved back to its source document and extractor.

    Needs blob_store because document text may live in blob storage rather
    than inline (documents.text is NULL for anything written since M1.5) —
    without it, a provenance chain couldn't reach the actual source text,
    which is the entire point of P1.
    """
    rows = session.scalars(
        select(ProvenanceORM).where(
            ProvenanceORM.target_table == target_table, ProvenanceORM.target_id == target_id
        )
    ).all()

    # A chain touching the same document more than once (common: several
    # mentions from one article) should only fetch that document's text
    # once, not once per provenance row.
    document_text_cache: dict[int, str] = {}

    entries: list[ProvenanceEntry] = []
    for row in rows:
        document = session.get(DocumentORM, row.document_id)
        extractor = session.get(ExtractorVersionORM, row.extractor_version_id)
        mention = session.get(MentionORM, row.mention_id) if row.mention_id else None

        if document.id not in document_text_cache:
            document_text_cache[document.id] = resolve_document_text(document, blob_store)
        document_text = document_text_cache[document.id]

        entries.append(
            ProvenanceEntry(
                target_table=row.target_table,
                target_id=row.target_id,
                document_id=document.id,
                document_source=document.source,
                document_text=document_text,
                mention_text=mention.text if mention else None,
                mention_start=mention.start_offset if mention else None,
                mention_end=mention.end_offset if mention else None,
                extractor_name=extractor.name,
                extractor_version=extractor.version,
                created_at=row.created_at,
            )
        )
    return entries


def format_provenance_chain(entries: list[ProvenanceEntry]) -> str:
    """Human-readable rendering for the `provenance show` CLI command."""
    if not entries:
        return "(no provenance recorded)"

    lines: list[str] = []
    for entry in entries:
        lines.append(f"{entry.target_table}[{entry.target_id}]")
        lines.append(f"  extracted by: {entry.extractor_name} v{entry.extractor_version} at {entry.created_at}")
        lines.append(f"  document: [{entry.document_id}] source={entry.document_source}")
        if entry.mention_text is not None:
            context_start = max(entry.mention_start - 20, 0)
            context_end = min(entry.mention_end + 20, len(entry.document_text))
            before = entry.document_text[context_start : entry.mention_start]
            span = entry.document_text[entry.mention_start : entry.mention_end]
            after = entry.document_text[entry.mention_end : context_end]
            lines.append(f"  source text: ...{before}[{span}]{after}...")
        else:
            lines.append(f"  source text: {entry.document_text[:80]}...")
        lines.append("")
    return "\n".join(lines)


def _to_extractor_version(row: ExtractorVersionORM) -> ExtractorVersion:
    return ExtractorVersion(id=row.id, name=row.name, version=row.version, description=row.description)


def _to_fact(row: FactORM) -> Fact:
    return Fact(
        id=row.id,
        fact_type=row.fact_type,
        subject_table=row.subject_table,
        subject_id=row.subject_id,
        payload=row.payload,
        extractor_version_id=row.extractor_version_id,
        supersedes_id=row.supersedes_id,
        retracted=row.retracted,
    )


def _to_mention(row: MentionORM) -> Mention:
    return Mention(
        id=row.id,
        document_id=row.document_id,
        text=row.text,
        start=row.start_offset,
        end=row.end_offset,
        object_type=row.object_type,
        extractor_version_id=row.extractor_version_id,
        retracted=row.retracted,
    )


def _to_entity(row: EntityORM) -> Entity:
    return Entity(
        id=row.id,
        object_type=row.object_type,
        canonical_name=row.canonical_name,
        properties=row.properties,
        supersedes_id=row.supersedes_id,
        retracted=row.retracted,
        retracted_reason=row.retracted_reason,
    )


def _to_link(row: LinkORM) -> Link:
    return Link(
        id=row.id,
        link_type=row.link_type,
        from_entity_id=row.from_entity_id,
        to_entity_id=row.to_entity_id,
        extractor_version_id=row.extractor_version_id,
        confidence=row.confidence,
        retracted=row.retracted,
    )
