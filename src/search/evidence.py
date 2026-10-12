"""Private, bounded entity search over an already loaded offline snapshot.

This is a read adapter, not a store, API, blob loader, or new identity scheme.
It uses the existing core models, language adapters, and search comparison.
See ADR 0034 for the trust boundary and the deliberately unconnected pieces.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Literal
from urllib.parse import urlsplit

from src.core.models import Document, Entity, ExtractorVersion, Mention
from src.lang import REGISTRY, AdapterRegistry, TextSpan
from src.search.name_matching import MatchKind, normalized_match

MAX_ENTITIES = 1000
MAX_DOCUMENTS = 500
MAX_MENTIONS = 10000
MAX_SOURCE_CHARACTERS = 2_000_000
MAX_NAME_CHARACTERS = 512
MAX_QUERY_CHARACTERS = 256
MAX_RESULTS = 50
MAX_EVIDENCE_PER_ENTITY = 5
MAX_SPAN_CHARACTERS = 600
MAX_TRANSLATION_CHARACTERS = 1200
READER_VERSION = "1.0.0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceInputError(ValueError):
    """The snapshot cannot prove the evidence it asks us to return."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    document: Document
    language: str
    text_sha256: str
    text_length: int


@dataclass(frozen=True, slots=True)
class SearchEntity:
    entity: Entity
    language: str


@dataclass(frozen=True, slots=True)
class EntityMention:
    """A detached read of the existing entity_mentions link, not a new fact."""

    entity_id: int
    mention_id: int


@dataclass(frozen=True, slots=True)
class VerifiedAlias:
    entity_id: int
    text: str
    language: str
    evidence_mention_id: int
    verifier: str
    verification_ref: str


@dataclass(frozen=True, slots=True)
class CachedEvidenceTranslation:
    """Detached artifact + exact use; fields mirror ADR 0031's separation.

The caller must actually possess the referenced cache artifact. A hash proves
text alignment, not that a provider generated a claimed output. Fixture rows
must be labelled synthetic_fixture and are never called machine translations.
"""

    document_id: int
    document_text_sha256: str
    start: int
    end: int
    source_text_sha256: str
    source_lang: str
    target_lang: str
    artifact_ref: str
    provider: str
    provider_version: str
    settings_sha256: str
    output_text: str
    record_origin: Literal["persisted_cache", "synthetic_fixture"]


@dataclass(frozen=True, slots=True)
class EvidenceCorpus:
    documents: tuple[SourceDocument, ...]
    entities: tuple[SearchEntity, ...]
    mentions: tuple[Mention, ...]
    memberships: tuple[EntityMention, ...]
    extractor_versions: tuple[ExtractorVersion, ...]
    aliases: tuple[VerifiedAlias, ...] = ()
    translations: tuple[CachedEvidenceTranslation, ...] = ()
    data_kind: Literal["synthetic_fixture", "offline_snapshot"] = "offline_snapshot"


@dataclass(frozen=True, slots=True)
class NameSurface:
    kind: Literal["original", "romanization", "verified_alias"]
    text: str
    language: str
    producer: str | None = None
    producer_version: str | None = None
    verifier: str | None = None
    verification_ref: str | None = None
    evidence_mention_id: int | None = None


@dataclass(frozen=True, slots=True)
class NameMatch:
    name: NameSurface
    match_kind: MatchKind


def text_sha256(text: str) -> str:
    """Hash original UTF-8 bytes, never the normalized comparison key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, field: str, maximum: int = 1024) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise EvidenceInputError(f"{field} must be non-empty text, at most {maximum} characters")
    _unicode(value, field)


def _unicode(value: str, field: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise EvidenceInputError(f"{field} must contain valid Unicode, not unpaired surrogates") from exc


def _language(value: object, registry: AdapterRegistry) -> None:
    _text(value, "language", 32)
    try:
        registry.get(value)
    except ValueError as exc:
        raise EvidenceInputError(str(exc)) from exc


def _integer(value: object, field: str, minimum: int = 1) -> None:
    # bool is an int subclass in Python; True must not become document 1.
    if type(value) is not int or value < minimum:
        raise EvidenceInputError(f"{field} must be an integer >= {minimum}")


def _flag(value: object, field: str) -> None:
    if type(value) is not bool:
        raise EvidenceInputError(f"{field} must be a boolean")


def _hash(value: object, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise EvidenceInputError(f"{field} must be a lowercase SHA-256 hex digest")


def _source_url(value: str) -> None:
    """Apply the existing dashboard URL safety boundary without importing UI contracts."""
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise EvidenceInputError("source URL must not contain whitespace or control characters")
    if any(char in value for char in "\\\"'<>`"):
        raise EvidenceInputError("source URL must not contain backslashes or HTML delimiters")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        parts.port
    except ValueError as exc:
        raise EvidenceInputError("source URL authority is malformed") from exc
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or not hostname:
        raise EvidenceInputError("source URL must be absolute HTTP(S)")
    if parts.username is not None or parts.password is not None:
        raise EvidenceInputError("source URL must not contain credentials")


def _unique(rows: tuple, key, field: str) -> dict:
    result = {}
    for row in rows:
        row_id = key(row)
        _integer(row_id, field)
        if row_id in result:
            raise EvidenceInputError(f"duplicate {field}: {row_id}")
        result[row_id] = row
    return result


def _span(text: str, start: int, end: int, field: str) -> None:
    _integer(start, f"{field}.start", 0)
    _integer(end, f"{field}.end")
    if not start < end <= len(text):
        raise EvidenceInputError(f"{field} must satisfy 0 <= start < end <= source length")


def validate_corpus(corpus: EvidenceCorpus, registry: AdapterRegistry = REGISTRY) -> None:
    """Fail closed on corrupt or contradictory inputs, even for unmatched rows.

    No partial result is returned for a corpus with stale text, missing
    references, ambiguous cache selections, or impossible alias attestations.
    A caller loading real blobs must handle retrieval failures before here.
    """
    if not isinstance(corpus.data_kind, str) or corpus.data_kind not in {"synthetic_fixture", "offline_snapshot"}:
        raise EvidenceInputError("unsupported data_kind")
    bounds = (
        (corpus.entities, MAX_ENTITIES, "entities"),
        (corpus.documents, MAX_DOCUMENTS, "documents"),
        (corpus.mentions, MAX_MENTIONS, "mentions"),
        (corpus.memberships, MAX_MENTIONS, "memberships"),
        (corpus.extractor_versions, MAX_MENTIONS, "extractor_versions"),
        (corpus.aliases, MAX_ENTITIES * 20, "aliases"),
        (corpus.translations, MAX_MENTIONS, "translations"),
    )
    for rows, maximum, label in bounds:
        if len(rows) > maximum:
            raise EvidenceInputError(f"too many {label}; maximum is {maximum}")

    documents = _unique(corpus.documents, lambda row: row.document.id, "document.id")
    entities = _unique(corpus.entities, lambda row: row.entity.id, "entity.id")
    mentions = _unique(corpus.mentions, lambda row: row.id, "mention.id")
    extractors = _unique(corpus.extractor_versions, lambda row: row.id, "extractor.id")
    total_characters = 0
    for row in documents.values():
        doc = row.document
        _text(doc.source, "document.source")
        _text(doc.content_hash, "document.content_hash", 128)
        _text(doc.text, "document.text", MAX_SOURCE_CHARACTERS)
        _hash(row.text_sha256, "document.text_sha256")
        _integer(row.text_length, "document.text_length")
        _flag(doc.retracted, "document.retracted")
        _language(row.language, registry)
        total_characters += len(doc.text)
        if total_characters > MAX_SOURCE_CHARACTERS:
            raise EvidenceInputError("snapshot exceeds total source character limit")
        if row.text_length != len(doc.text) or row.text_sha256 != text_sha256(doc.text):
            raise EvidenceInputError(f"document {doc.id}: source text hash or length mismatch")
        if doc.url is not None:
            _text(doc.url, "document.url", 2048)
            _source_url(doc.url)

    for row in entities.values():
        _text(row.entity.canonical_name, "entity.canonical_name", MAX_NAME_CHARACTERS)
        _text(row.entity.object_type, "entity.object_type", 100)
        _flag(row.entity.retracted, "entity.retracted")
        _language(row.language, registry)
    for extractor in extractors.values():
        _text(extractor.name, "extractor.name", 100)
        _text(extractor.version, "extractor.version", 100)
    for mention in mentions.values():
        _integer(mention.document_id, "mention.document_id")
        _integer(mention.extractor_version_id, "mention.extractor_version_id")
        if mention.document_id not in documents or mention.extractor_version_id not in extractors:
            raise EvidenceInputError(f"mention {mention.id}: missing document or extractor")
        source = documents[mention.document_id].document.text
        _span(source, mention.start, mention.end, f"mention {mention.id}")
        _text(mention.text, "mention.text", MAX_SOURCE_CHARACTERS)
        _text(mention.object_type, "mention.object_type", 100)
        _flag(mention.retracted, "mention.retracted")
        if source[mention.start:mention.end] != mention.text:
            raise EvidenceInputError(f"mention {mention.id}: offsets do not match exact source text")

    memberships = set()
    live_owners = {}
    for link in corpus.memberships:
        _integer(link.entity_id, "membership.entity_id")
        _integer(link.mention_id, "membership.mention_id")
        pair = (link.entity_id, link.mention_id)
        if pair in memberships:
            raise EvidenceInputError("duplicate entity/mention membership")
        if link.entity_id not in entities or link.mention_id not in mentions:
            raise EvidenceInputError("membership references a missing entity or mention")
        if entities[link.entity_id].entity.object_type != mentions[link.mention_id].object_type:
            raise EvidenceInputError("membership object types disagree")
        if not entities[link.entity_id].entity.retracted and _live_mention(mentions[link.mention_id], documents):
            if link.mention_id in live_owners and live_owners[link.mention_id] != link.entity_id:
                raise EvidenceInputError("one live mention belongs to multiple live entities; snapshot scope is ambiguous")
            live_owners[link.mention_id] = link.entity_id
        memberships.add(pair)

    alias_counts = {}
    alias_keys = set()
    for alias in corpus.aliases:
        _integer(alias.entity_id, "alias.entity_id")
        _integer(alias.evidence_mention_id, "alias.evidence_mention_id")
        if (alias.entity_id, alias.evidence_mention_id) not in memberships:
            raise EvidenceInputError("verified alias must reference its entity's linked evidence")
        _text(alias.text, "alias.text", MAX_NAME_CHARACTERS)
        _text(alias.verifier, "alias.verifier", 100)
        _text(alias.verification_ref, "alias.verification_ref", 512)
        _language(alias.language, registry)
        key = (alias.entity_id, alias.language, alias.text)
        if key in alias_keys:
            raise EvidenceInputError("duplicate verified alias; choose one attestation explicitly")
        alias_keys.add(key)
        alias_counts[alias.entity_id] = alias_counts.get(alias.entity_id, 0) + 1
        if alias_counts[alias.entity_id] > 20:
            raise EvidenceInputError("at most 20 verified aliases per entity")

    selected_translations = set()
    artifact_outputs = {}
    artifact_refs = {}
    for translation in corpus.translations:
        _integer(translation.document_id, "translation.document_id")
        if translation.document_id not in documents:
            raise EvidenceInputError("translation references a missing document")
        row = documents[translation.document_id]
        _hash(translation.document_text_sha256, "translation.document_text_sha256")
        if translation.document_text_sha256 != row.text_sha256:
            raise EvidenceInputError("translation belongs to a different document text version")
        _span(row.document.text, translation.start, translation.end, "translation")
        _hash(translation.source_text_sha256, "translation.source_text_sha256")
        original = row.document.text[translation.start:translation.end]
        if text_sha256(original) != translation.source_text_sha256:
            raise EvidenceInputError("translation source span hash mismatch")
        if translation.source_lang != row.language:
            raise EvidenceInputError("translation source language disagrees with document")
        _language(translation.target_lang, registry)
        for label in ("artifact_ref", "provider", "provider_version"):
            _text(getattr(translation, label), f"translation.{label}", 512)
        _hash(translation.settings_sha256, "translation.settings_sha256")
        if not isinstance(translation.output_text, str) or len(translation.output_text) > 10000:
            raise EvidenceInputError("translation output must be text, at most 10000 characters")
        _unicode(translation.output_text, "translation.output_text")
        if not isinstance(translation.record_origin, str) or translation.record_origin not in {"persisted_cache", "synthetic_fixture"}:
            raise EvidenceInputError("translation must identify its cache or fixture origin")
        if corpus.data_kind == "synthetic_fixture" and translation.record_origin != "synthetic_fixture":
            raise EvidenceInputError("synthetic fixture cannot claim persisted machine translation")
        selection = (translation.document_id, translation.start, translation.end, translation.target_lang)
        if selection in selected_translations:
            raise EvidenceInputError("ambiguous cached translation; select one artifact for each span/target")
        selected_translations.add(selection)
        address = (
            translation.source_text_sha256, translation.source_lang, translation.target_lang,
            translation.provider, translation.provider_version, translation.settings_sha256,
        )
        if address in artifact_outputs and artifact_outputs[address] != translation.output_text:
            raise EvidenceInputError("one translation content address has conflicting outputs")
        artifact_outputs[address] = translation.output_text
        identity = (address, translation.output_text, translation.record_origin)
        if translation.artifact_ref in artifact_refs and artifact_refs[translation.artifact_ref] != identity:
            raise EvidenceInputError("one translation artifact reference has conflicting provenance")
        artifact_refs[translation.artifact_ref] = identity


def _live_mention(mention: Mention, documents: dict[int, SourceDocument]) -> bool:
    return not mention.retracted and not documents[mention.document_id].document.retracted


def _names(
    row: SearchEntity, aliases: list[VerifiedAlias], registry: AdapterRegistry,
) -> list[NameSurface]:
    names = [NameSurface("original", row.entity.canonical_name, row.language)]
    adapter = registry.get(row.language)
    # EnglishAdapter.romanize is a protocol no-op, not an alternate name.
    # Only keep candidates whose detected script/language differs from source.
    for candidate in adapter.romanize(row.entity.canonical_name):
        target = registry.detect(candidate)
        if target.code != row.language:
            names.append(NameSurface(
                "romanization", candidate, f"{row.language}-Latn",
                producer=f"{type(adapter).__name__}.romanize",
            ))
    for alias in sorted(aliases, key=lambda item: (item.language, item.text, item.verification_ref)):
        names.append(NameSurface(
            "verified_alias", alias.text, alias.language,
            verifier=alias.verifier, verification_ref=alias.verification_ref,
            evidence_mention_id=alias.evidence_mention_id,
        ))
    return names


def _matches(query: str, names: list[NameSurface], registry: AdapterRegistry) -> list[NameMatch]:
    query_adapter = registry.detect(query)
    query_key = query_adapter.normalize(query)
    matches = []
    for name in names:
        # Both sides use the query language's comparison rules; source strings
        # remain intact, including diacritics and presentation forms.
        kind = normalized_match(query_key, query_adapter.normalize(name.text))
        if kind is not None:
            matches.append(NameMatch(name, kind))
    return matches


def _window(
    source: str, mention: Mention, sentences: list[TextSpan], starts: list[int], maximum: int,
) -> tuple[TextSpan, TextSpan | None]:
    sentence = None
    index = bisect_right(starts, mention.start) - 1
    if index >= 0:
        candidate = sentences[index]
        if candidate.start <= mention.start and mention.end <= candidate.end:
            sentence = candidate
    if sentence is None:
        # A mention crossing a segmentation boundary is not a sentence. Keep
        # its exact span instead of inventing a boundary or losing evidence.
        return TextSpan.from_source(source, mention.start, mention.end), None
    if len(sentence.text) <= maximum:
        return sentence, sentence
    spare = maximum - len(mention.text)
    start = max(sentence.start, mention.start - spare // 2)
    end = min(sentence.end, start + maximum)
    start = max(sentence.start, end - maximum)
    return TextSpan.from_source(source, start, end), sentence


def _translation_for(
    translation: CachedEvidenceTranslation | None,
) -> tuple[str, dict | None]:
    if translation is None:
        return "unavailable_for_exact_window", None
    if len(translation.output_text) > MAX_TRANSLATION_CHARACTERS:
        return "translation_output_exceeds_limit", None
    details = asdict(translation)
    if translation.record_origin == "synthetic_fixture":
        details["kind"] = "synthetic_fixture_translation"
        details["machine_generated"] = False
        return "fixture_only", details
    details["kind"] = "cached_machine_translation"
    details["machine_generated"] = True
    status = "cached_empty" if not translation.output_text else "cached"
    return status, details


def search_evidence(
    corpus: EvidenceCorpus,
    query: str,
    *,
    limit: int = 20,
    evidence_per_entity: int = 3,
    span_characters: int = 280,
    target_language: str = "en",
    registry: AdapterRegistry = REGISTRY,
) -> dict:
    """Return JSON-ready, deterministically ordered private evidence results.

    Bounds are hard ceilings, not suggestions. Exact source offsets are
    half-open Python Unicode code points. No network, database, cache fill,
    identity adoption, source text mutation, or external publication occurs.
    """
    if not isinstance(query, str) or len(query) > MAX_QUERY_CHARACTERS:
        raise EvidenceInputError(f"query must be text, at most {MAX_QUERY_CHARACTERS} characters")
    _unicode(query, "query")
    for value, maximum, label in (
        (limit, MAX_RESULTS, "limit"),
        (evidence_per_entity, MAX_EVIDENCE_PER_ENTITY, "evidence_per_entity"),
        (span_characters, MAX_SPAN_CHARACTERS, "span_characters"),
    ):
        _integer(value, label)
        if value > maximum:
            raise EvidenceInputError(f"{label} exceeds maximum {maximum}")
    _language(target_language, registry)
    validate_corpus(corpus, registry)
    documents = {row.document.id: row for row in corpus.documents}
    mentions = {row.id: row for row in corpus.mentions}
    extractors = {row.id: row for row in corpus.extractor_versions}
    linked = {}
    for link in corpus.memberships:
        mention = mentions[link.mention_id]
        if _live_mention(mention, documents):
            linked.setdefault(link.entity_id, []).append(mention)
    aliases = {}
    for alias in corpus.aliases:
        if _live_mention(mentions[alias.evidence_mention_id], documents):
            aliases.setdefault(alias.entity_id, []).append(alias)
    translations = {}
    for translation in corpus.translations:
        key = (translation.document_id, translation.start, translation.end, translation.target_lang)
        translations[key] = translation

    candidates = []
    kind_rank = {"original": 0, "verified_alias": 1, "romanization": 2}
    for row in corpus.entities:
        if row.entity.retracted:
            continue
        names = _names(row, aliases.get(row.entity.id, []), registry)
        matches = _matches(query, names, registry)
        if not matches:
            continue
        best = min((match.match_kind != "exact", kind_rank[match.name.kind]) for match in matches)
        candidates.append((best, row.entity.id, row, names, matches))
    candidates.sort(key=lambda item: (item[0], item[1]))

    results = []
    sentence_cache = {}
    for _, entity_id, row, names, matches in candidates[:limit]:
        evidence = []
        seen_windows = set()
        oversized_mentions = 0
        entity_mentions = sorted(
            linked.get(entity_id, []), key=lambda item: (item.document_id, item.start, item.end, item.id),
        )
        for mention in entity_mentions:
            if len(mention.text) > span_characters:
                oversized_mentions += 1
                continue
            source = documents[mention.document_id]
            adapter = registry.get(source.language)
            if mention.document_id not in sentence_cache:
                sentences = adapter.segment_sentences(source.document.text)
                sentence_cache[mention.document_id] = (sentences, [span.start for span in sentences])
            sentences, starts = sentence_cache[mention.document_id]
            window, sentence = _window(
                source.document.text, mention, sentences, starts, span_characters,
            )
            key = (mention.document_id, window.start, window.end)
            if key in seen_windows:
                continue
            seen_windows.add(key)
            # We count additional windows but don't materialize their output.
            if len(evidence) >= evidence_per_entity:
                continue
            cached = translations.get((*key, target_language))
            translation_status, translation = _translation_for(cached)
            clipped = sentence is not None and (window.start, window.end) != (sentence.start, sentence.end)
            span_kind = "mention_span" if sentence is None else "sentence_window" if clipped else "sentence"
            extractor = extractors[mention.extractor_version_id]
            evidence.append({
                "document_id": mention.document_id,
                "source": source.document.source,
                "url": source.document.url,
                "content_hash": source.document.content_hash,
                "document_text_sha256": source.text_sha256,
                "document_text_length": source.text_length,
                "language": source.language,
                "start": window.start,
                "end": window.end,
                "text": window.text,
                "span_sha256": text_sha256(window.text),
                "span_kind": span_kind,
                "truncated": clipped,
                "sentence_start": sentence.start if sentence else None,
                "sentence_end": sentence.end if sentence else None,
                "segmenter": asdict(adapter.segmentation),
                "mention": {
                    "id": mention.id, "text": mention.text, "start": mention.start,
                    "end": mention.end, "extractor_version_id": extractor.id,
                    "extractor_name": extractor.name, "extractor_version": extractor.version,
                },
                "translation_status": translation_status,
                "translation": translation,
            })
        results.append({
            "entity_id": entity_id,
            "object_type": row.entity.object_type,
            "names": [asdict(name) for name in names],
            "matched_names": [asdict(match) for match in matches],
            "evidence": evidence,
            "evidence_coverage": {
                "active_linked_mentions": len(entity_mentions),
                "unique_windows": len(seen_windows),
                "has_more_windows": len(seen_windows) > len(evidence),
                "mentions_exceeding_span_limit": oversized_mentions,
                "status": "available" if evidence else "no_bounded_live_evidence",
            },
        })
    return {
        "schema_version": "offline-evidence-v1",
        "reader": {"name": "offline_evidence_search", "version": READER_VERSION},
        "data_kind": corpus.data_kind,
        "scope": "caller_supplied_snapshot",
        "entity_id_scope": "snapshot_local",
        "access_scope": "private_local_only",
        "query": query,
        "target_language": target_language,
        "limits": {"results": limit, "evidence_per_entity": evidence_per_entity, "span_characters": span_characters},
        "matched_entity_count": len(candidates),
        "has_more_entities": len(candidates) > len(results),
        "results": results,
    }
