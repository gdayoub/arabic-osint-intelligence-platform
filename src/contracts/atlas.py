"""Additive metadata archive contract. The dashboard v1 contract stays frozen.

``complete`` means every currently processed, non-retracted document in one
read snapshot, including missing countries and dates. It never means all
collected documents, historical classifications, or full source text.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Annotated, Any, Iterable, Literal, Mapping, Self

from pydantic import Field, field_validator, model_validator

from src.contracts.dashboard import (
    ContractModel,
    NonEmptyText,
    PublicHttpUrl,
    Timestamp,
    validate_snapshot_bundle,
)

ATLAS_VERSION = 1
ATLAS_SCOPE = "current_processed_live_documents"
SnapshotId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class AtlasClassification(ContractModel):
    name: NonEmptyText
    version: NonEmptyText


class AtlasArticle(ContractModel):
    id: NonEmptyText
    title: NonEmptyText
    title_en: NonEmptyText | None
    source: NonEmptyText
    url: PublicHttpUrl | None
    topic: NonEmptyText
    escalation: NonEmptyText
    country: NonEmptyText | None
    published_date: Timestamp | None
    collected_at: Timestamp | None
    entity_ids: list[NonEmptyText]

    @model_validator(mode="after")
    def memberships_are_unique(self) -> Self:
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("article entity memberships must be unique")
        return self


class AtlasEntity(ContractModel):
    id: NonEmptyText
    name: NonEmptyText
    name_en: NonEmptyText | None
    object_type: NonEmptyText
    surface_forms: Annotated[list[NonEmptyText], Field(min_length=1)]
    romanizations: list[NonEmptyText]

    @model_validator(mode="after")
    def search_forms_are_unique(self) -> Self:
        if len(self.surface_forms) != len(set(self.surface_forms)):
            raise ValueError("entity surface forms must be unique")
        if len(self.romanizations) != len(set(self.romanizations)):
            raise ValueError("entity romanizations must be unique")
        return self


def atlas_snapshot_id(payload: Mapping[str, Any]) -> str:
    """Hash the complete public payload except the hash itself.

    This identifies bytes/metadata, not a durable entity or historical state.
    JSON keys are sorted so dictionary insertion order cannot change identity.
    List order remains meaningful; the producer emits it deterministically.
    """
    content = {key: value for key, value in payload.items() if key != "snapshot_id"}
    encoded = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class AtlasSnapshot(ContractModel):
    atlas_version: Literal[1]
    generated_at: Timestamp
    snapshot_id: SnapshotId
    complete: Literal[True]
    scope: Literal["current_processed_live_documents"]
    entity_id_scope: Literal["snapshot_local"]
    translation_scope: Literal["cached_machine_translation"]
    classification: AtlasClassification
    articles: list[AtlasArticle]
    entities: list[AtlasEntity]

    @field_validator("atlas_version", "complete", mode="before")
    @classmethod
    def literal_types_are_strict(cls, value: Any, info: Any) -> Any:
        # Python considers True == 1. Literal validation alone inherits that
        # equality, even under strict model config; the wire types differ.
        expected_type = bool if info.field_name == "complete" else int
        if type(value) is not expected_type:
            raise ValueError(f"{info.field_name} has the wrong JSON type")
        return value

    @model_validator(mode="after")
    def consistent_references(self) -> Self:
        article_ids = [article.id for article in self.articles]
        entity_ids = [entity.id for entity in self.entities]
        if len(article_ids) != len(set(article_ids)):
            raise ValueError("archive article identities must be unique")
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("archive entity identities must be unique")
        known = set(entity_ids)
        used: set[str] = set()
        for article in self.articles:
            if not set(article.entity_ids).issubset(known):
                raise ValueError("article refers to an absent entity")
            used.update(article.entity_ids)
        if used != known:
            raise ValueError("archive entity has no current article membership")
        if self.snapshot_id != atlas_snapshot_id(self.model_dump(mode="json")):
            raise ValueError("archive snapshot_id does not match its contents")
        return self


def validate_atlas_snapshot(
    payload: Mapping[str, Any], *, expected_document_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """Validate public shape and, when supplied, exact producer membership.

    A count cannot prove completeness. The database producer supplies the IDs
    selected by the current-processing predicate in the same read snapshot.
    This private expectation is not an additional public metadata field.
    """
    model = AtlasSnapshot.model_validate(payload)
    if expected_document_ids is not None:
        expected = set(expected_document_ids)
        if {article.id for article in model.articles} != expected:
            raise ValueError("archive must contain exactly the current processed document IDs")
    return model.model_dump(mode="json")


def build_atlas_snapshot(
    *,
    generated_at: str,
    classification: Mapping[str, str],
    articles: Iterable[Mapping[str, Any]],
    entities: Iterable[Mapping[str, Any]],
    expected_document_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic candidate from already selected public metadata.

    No input is silently deduplicated: duplicate identities or memberships are
    a producer error, not a reason to drop data while claiming completeness.
    """
    article_rows = [AtlasArticle.model_validate(row).model_dump(mode="json") for row in articles]
    entity_rows = [AtlasEntity.model_validate(row).model_dump(mode="json") for row in entities]
    for article in article_rows:
        article["entity_ids"].sort()
    for entity in entity_rows:
        entity["surface_forms"].sort()
        entity["romanizations"].sort()
    payload = {
        "atlas_version": ATLAS_VERSION,
        "generated_at": generated_at,
        "complete": True,
        "scope": ATLAS_SCOPE,
        "entity_id_scope": "snapshot_local",
        "translation_scope": "cached_machine_translation",
        "classification": dict(classification),
        "articles": sorted(article_rows, key=lambda row: row["id"]),
        "entities": sorted(entity_rows, key=lambda row: row["id"]),
    }
    payload["snapshot_id"] = atlas_snapshot_id(payload)
    return validate_atlas_snapshot(payload, expected_document_ids=expected_document_ids)


def _article_signature(article: Mapping[str, Any], *, archive: bool) -> tuple[Any, ...]:
    """The common v1 metadata, preserving null dates and original Unicode.

    v1 has no document IDs. These signatures verify its observable membership;
    exact ID membership is separately checked inside the archive producer.
    ``processed_at`` is v1's legacy label for collection time, not a claim
    about when classification happened.
    """
    return (
        article["title"], article["title_en"], article["source"], article["url"],
        article["topic"], article["escalation"], article["published_date"],
        article["collected_at" if archive else "processed_at"],
    )


def _require_sample_membership(
    sample: Iterable[Mapping[str, Any]],
    articles: Iterable[Mapping[str, Any]],
    *,
    label: str,
) -> None:
    expected = Counter(_article_signature(row, archive=True) for row in articles)
    observed = Counter(_article_signature(row, archive=False) for row in sample)
    if observed - expected:
        raise ValueError(f"{label} contains metadata absent from this archive snapshot")


def validate_atlas_bundle(
    dashboard: Mapping[str, Any],
    country_pages: Mapping[str, Mapping[str, Any]],
    atlas: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Check scope, full aggregates, and sampled membership before any write."""
    data, pages = validate_snapshot_bundle(dashboard, country_pages)
    archive = validate_atlas_snapshot(atlas)
    if archive["generated_at"] != data["generated_at"]:
        raise ValueError("archive and dashboard must belong to the same snapshot")
    articles = archive["articles"]
    if len(articles) != data["stats"]["total_processed"]:
        raise ValueError("complete archive must contain every current processed article")
    if Counter(row["topic"] for row in articles) != Counter({
        row["topic"]: row["count"] for row in data["topics"]["topics"]
    }):
        raise ValueError("archive topic counts do not match dashboard")
    if Counter(row["escalation"] for row in articles) != Counter(data["escalation"]["escalation"]):
        raise ValueError("archive escalation counts do not match dashboard")
    processed_sources = Counter(row["source"] for row in articles)
    if processed_sources - Counter(data["stats"]["sources"]):
        raise ValueError("archive sources exceed live dashboard source counts")

    countries = Counter(row["country"] for row in articles if row["country"] is not None)
    if countries != Counter({row["country"]: row["count"] for row in data["countries"]}):
        raise ValueError("archive countries do not match dashboard country membership")
    for country, page in pages.items():
        members = [row for row in articles if row["country"] == country]
        if Counter(row["source"] for row in members) != Counter(page["sources"]):
            raise ValueError(f"archive source counts do not match country {country}")
        if Counter(row["topic"] for row in members) != Counter({
            row["topic"]: row["count"] for row in page["topics"]
        }):
            raise ValueError(f"archive topic counts do not match country {country}")
        if Counter(row["escalation"] for row in members) != Counter(page["escalation"]):
            raise ValueError(f"archive escalation counts do not match country {country}")
        _require_sample_membership(page["articles"], members, label=f"country {country}")

    # The recent feed also contains unprocessed live documents. Only its
    # completed rows belong to the archive; those must match including country.
    for country in {row["country"] for row in data["recent"]}:
        sample = [row for row in data["recent"] if row["country"] == country
                  and row["topic"] is not None and row["escalation"] is not None]
        members = [row for row in articles if row["country"] == country]
        _require_sample_membership(sample, members, label="recent feed")
    return data, pages, archive
