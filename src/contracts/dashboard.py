"""Strict producer contract for the public dashboard snapshots.

The browser applications still own HTML escaping.  These models preserve
external text exactly as stored while rejecting URLs that are not absolute
HTTP(S) links.  See ADR 0018.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal, Mapping, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0.0"
SCHEMA_VERSION = 1
PYDANTIC_PIN = "pydantic==2.12.5"
PUBLIC_HTTP_URL_PATTERN = r"""^[hH][tT][tT][pP][sS]?://[^\s\\"'<>`]+$"""


def _validate_public_http_url(value: str) -> str:
    """Validate a browser link without normalizing the evidence value."""
    if value != value.strip():
        raise ValueError("URL must not have leading or trailing whitespace")
    if any(character.isspace() for character in value):
        raise ValueError("URL must not contain whitespace")
    if "\\" in value:
        raise ValueError("URL must not contain backslashes")
    if any(character in value for character in ('"', "'", "<", ">", "`")):
        raise ValueError("URL must not contain raw HTML attribute delimiters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("URL must not contain control characters")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing port also detects malformed or out-of-range port syntax.
        parsed.port
    except ValueError as exc:
        raise ValueError("URL authority is malformed") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if not parsed.netloc or hostname is None:
        raise ValueError("URL must be absolute and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("public source URLs must not contain credentials")
    return value


def _validate_timestamp(value: str) -> str:
    """Require an ISO 8601 timestamp with an explicit UTC offset."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _validate_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc
    return value


NonEmptyText = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
PublicHttpUrl = Annotated[
    str,
    AfterValidator(_validate_public_http_url),
    Field(
        json_schema_extra={
            "format": "uri",
            "pattern": PUBLIC_HTTP_URL_PATTERN,
            "x-allowed-schemes": ["http", "https"],
        }
    ),
]
Timestamp = Annotated[
    str,
    AfterValidator(_validate_timestamp),
    Field(json_schema_extra={"format": "date-time"}),
]
CalendarDate = Annotated[
    str,
    AfterValidator(_validate_date),
    Field(json_schema_extra={"format": "date"}),
]
CountrySlug = Annotated[
    str,
    Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]


class ContractModel(BaseModel):
    """The common strictness policy for every public JSON object."""

    model_config = ConfigDict(strict=True, extra="forbid")


class TopicCount(ContractModel):
    topic: NonEmptyText
    count: NonNegativeInt


class DailyCount(ContractModel):
    date: CalendarDate
    count: NonNegativeInt


class RankedMention(ContractModel):
    name: NonEmptyText
    count: PositiveInt


class RankedEntity(ContractModel):
    name: NonEmptyText
    count: PositiveInt
    surface_forms: list[NonEmptyText]

    @model_validator(mode="after")
    def surface_forms_are_unique(self) -> Self:
        if len(self.surface_forms) != len(set(self.surface_forms)):
            raise ValueError("entity surface forms must be unique")
        return self


class SnapshotStats(ContractModel):
    total_raw: NonNegativeInt
    total_processed: NonNegativeInt
    sources: dict[NonEmptyText, NonNegativeInt]

    @model_validator(mode="after")
    def counts_agree(self) -> Self:
        if self.total_processed > self.total_raw:
            raise ValueError("total_processed cannot exceed total_raw")
        if sum(self.sources.values()) != self.total_raw:
            raise ValueError("source counts must sum to total_raw")
        return self


class TopicDistribution(ContractModel):
    topics: list[TopicCount]

    @model_validator(mode="after")
    def topics_are_unique(self) -> Self:
        names = [item.topic for item in self.topics]
        if len(names) != len(set(names)):
            raise ValueError("topic distribution contains duplicate topics")
        return self


class EscalationDistribution(ContractModel):
    escalation: dict[NonEmptyText, NonNegativeInt]


class RecentArticle(ContractModel):
    title: NonEmptyText
    title_en: NonEmptyText | None
    source: NonEmptyText
    url: PublicHttpUrl | None
    topic: NonEmptyText | None
    escalation: NonEmptyText | None
    country: NonEmptyText | None
    ai_summary: NonEmptyText | None
    processed_at: Timestamp | None
    published_date: Timestamp | None


class MentionSummary(ContractModel):
    total: NonNegativeInt
    top: dict[NonEmptyText, list[RankedMention]]

    @model_validator(mode="after")
    def top_does_not_exceed_total(self) -> Self:
        displayed = sum(item.count for items in self.top.values() for item in items)
        if displayed > self.total:
            raise ValueError("top mention counts cannot exceed mention total")
        return self


class EntitySummary(ContractModel):
    total: NonNegativeInt
    top: dict[NonEmptyText, list[RankedEntity]]

    @model_validator(mode="after")
    def top_does_not_exceed_evidence_total(self) -> Self:
        # Entity ranking counts evidence mentions, not entities.  The list may
        # therefore sum above ``total``; only empty/non-empty consistency is
        # invariant at this contract layer.
        if self.total == 0 and any(self.top.values()):
            raise ValueError("an empty entity set cannot have ranked entities")
        return self


class ReviewEvidence(ContractModel):
    mention_id: PositiveInt
    text: NonEmptyText
    source: NonEmptyText
    url: PublicHttpUrl | None
    title: NonEmptyText


class ReviewItem(ContractModel):
    id: PositiveInt
    object_type: NonEmptyText
    score: UnitInterval
    threshold: UnitInterval
    distance: UnitInterval
    features: Annotated[dict[NonEmptyText, UnitInterval], Field(min_length=1)]
    left: ReviewEvidence
    right: ReviewEvidence

    @model_validator(mode="after")
    def pair_invariants_hold(self) -> Self:
        if self.left.mention_id >= self.right.mention_id:
            raise ValueError("review mention ids must be in ascending order")
        expected_distance = abs(self.score - self.threshold)
        if not math.isclose(self.distance, expected_distance, abs_tol=1e-12):
            raise ValueError("review distance must equal abs(score - threshold)")
        return self


class ReviewQueue(ContractModel):
    items: list[ReviewItem]

    @model_validator(mode="after")
    def item_ids_are_unique(self) -> Self:
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("review queue contains duplicate item ids")
        return self


class CountryIndexItem(ContractModel):
    country: NonEmptyText
    slug: CountrySlug
    count: PositiveInt


class DashboardSnapshot(ContractModel):
    generated_at: Timestamp
    schema_version: Literal[SCHEMA_VERSION]
    stats: SnapshotStats
    topics: TopicDistribution
    escalation: EscalationDistribution
    recent: list[RecentArticle]
    daily: list[DailyCount]
    mentions: MentionSummary
    entities: EntitySummary
    review_queue: ReviewQueue
    countries: list[CountryIndexItem]

    @model_validator(mode="after")
    def aggregate_invariants_hold(self) -> Self:
        topic_total = sum(item.count for item in self.topics.topics)
        if topic_total != self.stats.total_processed:
            raise ValueError("topic counts must sum to total_processed")
        if sum(self.escalation.escalation.values()) != self.stats.total_processed:
            raise ValueError("escalation counts must sum to total_processed")
        if len(self.recent) > self.stats.total_raw:
            raise ValueError("recent articles cannot exceed total_raw")
        if sum(item.count for item in self.daily) > self.stats.total_raw:
            raise ValueError("daily window counts cannot exceed total_raw")
        if sum(item.count for item in self.countries) > self.stats.total_processed:
            raise ValueError("country counts cannot exceed total_processed")

        daily_dates = [item.date for item in self.daily]
        if daily_dates != sorted(daily_dates) or len(daily_dates) != len(set(daily_dates)):
            raise ValueError("daily rows must have unique ascending dates")

        countries = [item.country for item in self.countries]
        slugs = [item.slug for item in self.countries]
        if len(countries) != len(set(countries)):
            raise ValueError("country index contains duplicate countries")
        if len(slugs) != len(set(slugs)):
            raise ValueError("country index contains duplicate slugs")
        return self


class CountryArticle(ContractModel):
    title: NonEmptyText
    title_en: NonEmptyText | None
    source: NonEmptyText
    url: PublicHttpUrl | None
    topic: NonEmptyText
    escalation: NonEmptyText
    published_date: Timestamp | None
    processed_at: Timestamp | None


class CountrySnapshot(ContractModel):
    generated_at: Timestamp
    schema_version: Literal[SCHEMA_VERSION]
    country: NonEmptyText
    slug: CountrySlug
    total: PositiveInt
    sources: dict[NonEmptyText, NonNegativeInt]
    topics: list[TopicCount]
    escalation: dict[NonEmptyText, NonNegativeInt]
    daily: list[DailyCount]
    articles: list[CountryArticle]

    @model_validator(mode="after")
    def aggregate_invariants_hold(self) -> Self:
        if sum(self.sources.values()) != self.total:
            raise ValueError("country source counts must sum to total")
        if sum(item.count for item in self.topics) != self.total:
            raise ValueError("country topic counts must sum to total")
        if sum(self.escalation.values()) != self.total:
            raise ValueError("country escalation counts must sum to total")
        if len(self.articles) > self.total:
            raise ValueError("country article sample cannot exceed total")
        if sum(item.count for item in self.daily) > self.total:
            raise ValueError("country daily counts cannot exceed total")

        topic_names = [item.topic for item in self.topics]
        if len(topic_names) != len(set(topic_names)):
            raise ValueError("country topic distribution contains duplicates")
        daily_dates = [item.date for item in self.daily]
        if daily_dates != sorted(daily_dates) or len(daily_dates) != len(set(daily_dates)):
            raise ValueError("country daily rows must have unique ascending dates")
        return self


def validate_dashboard_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready, strictly validated dashboard snapshot."""
    model = DashboardSnapshot.model_validate(payload)
    return model.model_dump(mode="json")


def validate_country_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready, strictly validated country snapshot."""
    model = CountrySnapshot.model_validate(payload)
    return model.model_dump(mode="json")


def validate_snapshot_bundle(
    dashboard: Mapping[str, Any],
    country_pages: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate cross-file references as one publication candidate."""
    dashboard_model = DashboardSnapshot.model_validate(dashboard)

    country_models: dict[str, CountrySnapshot] = {}
    for map_key, payload in country_pages.items():
        model = CountrySnapshot.model_validate(payload)
        if map_key != model.country:
            raise ValueError(
                f"country page map key {map_key!r} does not match {model.country!r}"
            )
        country_models[model.country] = model

    index_by_country = {item.country: item for item in dashboard_model.countries}
    if set(index_by_country) != set(country_models):
        raise ValueError("dashboard country index must match the country page set")

    for country, index_item in index_by_country.items():
        page = country_models[country]
        if page.generated_at != dashboard_model.generated_at:
            raise ValueError(f"country page timestamp does not match dashboard for {country}")
        if page.schema_version != dashboard_model.schema_version:
            raise ValueError(f"country page schema version does not match dashboard for {country}")
        if index_item.slug != page.slug or index_item.count != page.total:
            raise ValueError(f"dashboard country index does not match page for {country}")

    validated_dashboard = dashboard_model.model_dump(mode="json")
    validated_pages = {
        country: country_models[country].model_dump(mode="json")
        for country in sorted(country_models)
    }
    return validated_dashboard, validated_pages
