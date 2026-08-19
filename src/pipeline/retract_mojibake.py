"""Retracts documents whose text was stored with a broken character encoding.

A scraper bug (see `_resolve_encoding` in src/scraping/base_scraper.py)
preferred chardet's guess over the server's declared charset, and chardet
reads Arabic UTF-8 as Cyrillic often enough that some documents were stored
as mojibake — unreadable, misclassified, and translated into nonsense.

The scraper is fixed, but rows written before the fix are still corrupt.
This retracts them (P6: retract, never delete) so they stop appearing in
the dashboard, while the rows and their blobs stay on record.

Detection is deliberately narrow: Cyrillic characters in a document from an
Arabic-language source. Real Arabic news does quote Latin script and does
cover Russia, but it does not print Cyrillic — and the specific
UTF-8-as-windows-1251 failure produces Cyrillic in bulk, not one stray
letter. The threshold guards against a legitimate one-off mention.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from src.store.blob import BlobStore, get_blob_store
from src.store.database import get_core_session
from src.store.documents import resolve_document_text, retract_document
from src.store.orm import DocumentORM

logger = logging.getLogger("pipeline.retract_mojibake")

# U+0400–U+04FF. Mojibake from UTF-8-read-as-windows-1251 lands here.
_CYRILLIC = re.compile("[Ѐ-ӿ]")

RETRACTION_REASON = "Stored with a broken character encoding (UTF-8 decoded as Cyrillic); scraper fixed in _resolve_encoding"

# Fraction of characters that must be Cyrillic before a document is judged
# corrupt rather than merely quoting something.
_THRESHOLD = 0.10


@dataclass(slots=True)
class RetractionStats:
    scanned: int = 0
    retracted: int = 0
    document_ids: list[int] = field(default_factory=list)


def is_mojibake(text: str, threshold: float = _THRESHOLD) -> bool:
    if not text:
        return False
    cyrillic_count = len(_CYRILLIC.findall(text))
    return cyrillic_count / len(text) >= threshold


def run_retract_mojibake(dry_run: bool = True, blob_store: BlobStore | None = None) -> RetractionStats:
    blob_store = blob_store or get_blob_store()
    stats = RetractionStats()

    with get_core_session() as session:
        rows = session.scalars(select(DocumentORM).where(DocumentORM.retracted.is_(False))).all()
        for row in rows:
            stats.scanned += 1
            try:
                text = resolve_document_text(row, blob_store)
            except Exception:
                logger.exception("Could not load document %s; skipping", row.id)
                continue

            if not is_mojibake(text):
                continue

            stats.retracted += 1
            stats.document_ids.append(row.id)
            if not dry_run:
                retract_document(session, row.id, RETRACTION_REASON)

        if dry_run:
            session.rollback()

    return stats
