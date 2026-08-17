"""Read path for documents.

src/store/provenance.py is the sanctioned WRITE path — every write there
carries its own provenance row (P1). Reads don't need that guarantee, so
they live here instead, and this module is where text actually gets fetched
back out of blob storage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import Document, DocumentRef
from src.store.blob import BlobStore, decompress_text, sha256_text
from src.store.orm import DocumentORM


def resolve_document_text(row: DocumentORM, blob_store: BlobStore) -> str:
    """Get a document's text regardless of where it lives, and verify it
    survived the round trip intact.

    `row.text` still wins if set — that's the inline escape hatch (legacy
    rows, or anything written before blob storage existed). Otherwise fetch
    and decompress from the blob store, then re-check both invariants that
    were true at write time: the sha256 of the bytes we compressed, and the
    Unicode code-point length (P2 — this is what a corrupted or truncated
    blob would break silently if it weren't checked here).
    """
    if row.text is not None:
        return row.text

    text = decompress_text(blob_store.get(row.text_blob_key))

    actual_sha256 = sha256_text(text)
    if actual_sha256 != row.text_sha256:
        raise ValueError(
            f"document {row.id}: blob content hash mismatch "
            f"(expected {row.text_sha256}, got {actual_sha256}) — blob may be corrupted"
        )
    if len(text) != row.text_length:
        raise ValueError(
            f"document {row.id}: text length mismatch after decompression "
            f"(expected {row.text_length} code points, got {len(text)})"
        )
    return text


def load_document(session: Session, document_id: int, blob_store: BlobStore) -> Document:
    row = session.get(DocumentORM, document_id)
    if row is None:
        raise ValueError(f"no document with id {document_id}")
    return _to_document(row, resolve_document_text(row, blob_store))


def load_document_ref(session: Session, document_id: int) -> DocumentRef:
    row = session.get(DocumentORM, document_id)
    if row is None:
        raise ValueError(f"no document with id {document_id}")
    return _to_document_ref(row)


def iter_document_refs(
    session: Session,
    *,
    source: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> Iterator[DocumentRef]:
    stmt = select(DocumentORM).order_by(DocumentORM.collected_at.desc())
    if source is not None:
        stmt = stmt.where(DocumentORM.source == source)
    if since is not None:
        stmt = stmt.where(DocumentORM.collected_at >= since)
    if limit is not None:
        stmt = stmt.limit(limit)

    for row in session.scalars(stmt):
        yield _to_document_ref(row)


def get_document_ref_by_url(session: Session, url: str) -> DocumentRef | None:
    row = session.scalar(select(DocumentORM).where(DocumentORM.url == url))
    return _to_document_ref(row) if row is not None else None


def retract_document(session: Session, document_id: int, reason: str) -> None:
    """P6: retract, never delete. The blob is left untouched — deliberately,
    since it may be shared with other documents that have identical text.
    """
    row = session.get(DocumentORM, document_id)
    if row is None:
        raise ValueError(f"no document with id {document_id}")
    row.retracted = True
    row.retracted_reason = reason


def _to_document(row: DocumentORM, text: str) -> Document:
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


def _to_document_ref(row: DocumentORM) -> DocumentRef:
    return DocumentRef(
        id=row.id,
        source=row.source,
        content_hash=row.content_hash,
        text_blob_key=row.text_blob_key,
        text_sha256=row.text_sha256,
        text_length=row.text_length,
        url=row.url,
        published_at=row.published_at,
        collected_at=row.collected_at,
        retracted=row.retracted,
        retracted_reason=row.retracted_reason,
    )
