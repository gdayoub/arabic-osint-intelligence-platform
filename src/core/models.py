"""Core domain dataclasses: Document, Mention, Entity, Link, Fact, Provenance.

No SQLAlchemy, no Postgres, no ontology-file parsing. This module must stay
importable with nothing but the standard library. The persistence layer
(src/store/) is responsible for turning these into database rows and back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ExtractorVersion:
    id: int | None
    name: str
    version: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Document:
    id: int | None
    source: str
    text: str
    content_hash: str
    url: str | None = None
    published_at: datetime | None = None
    collected_at: datetime | None = None
    retracted: bool = False
    retracted_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """Document metadata without its text.

    For anything that lists, counts, or filters documents without needing to
    pay for a blob fetch (dashboards, iteration, dedup lookups). Extractors
    that actually read text want Document instead.
    """

    id: int | None
    source: str
    content_hash: str
    text_blob_key: str | None
    text_sha256: str | None
    text_length: int | None
    url: str | None = None
    published_at: datetime | None = None
    collected_at: datetime | None = None
    retracted: bool = False
    retracted_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Mention:
    id: int | None
    document_id: int
    text: str
    start: int
    end: int
    object_type: str
    extractor_version_id: int
    retracted: bool = False


@dataclass(frozen=True, slots=True)
class Entity:
    id: int | None
    object_type: str
    canonical_name: str
    properties: dict[str, Any]
    supersedes_id: int | None = None
    retracted: bool = False
    retracted_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Link:
    id: int | None
    link_type: str
    from_entity_id: int
    to_entity_id: int
    extractor_version_id: int
    confidence: float | None = None
    retracted: bool = False


@dataclass(frozen=True, slots=True)
class Fact:
    id: int | None
    fact_type: str
    subject_table: str
    subject_id: int
    payload: dict[str, Any]
    extractor_version_id: int
    supersedes_id: int | None = None
    retracted: bool = False


@dataclass(frozen=True, slots=True)
class Provenance:
    id: int | None
    target_table: str
    target_id: int
    document_id: int
    extractor_version_id: int
    mention_id: int | None = None
