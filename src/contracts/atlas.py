"""Independent, additive public archive contract; dashboard v1 stays unchanged."""
from __future__ import annotations

from typing import Literal, Self
from pydantic import model_validator
from src.contracts.dashboard import ContractModel, NonEmptyText, Timestamp, PublicHttpUrl


class AtlasArticle(ContractModel):
    id: NonEmptyText
    title: NonEmptyText
    title_en: str | None
    source: NonEmptyText
    url: PublicHttpUrl | None
    topic: str | None
    escalation: str | None
    country: str | None
    published_date: Timestamp | None
    collected_at: Timestamp | None
    entity_ids: list[str]


class AtlasEntity(ContractModel):
    id: NonEmptyText
    name: NonEmptyText
    name_en: str | None
    object_type: NonEmptyText
    surface_forms: list[str]
    romanizations: list[str]


class AtlasSnapshot(ContractModel):
    atlas_version: Literal[1]
    generated_at: Timestamp
    complete: Literal[True]
    classification: dict[str, str]
    articles: list[AtlasArticle]
    entities: list[AtlasEntity]

    @model_validator(mode="after")
    def consistent_references(self) -> Self:
        article_ids = [article.id for article in self.articles]
        entity_ids = [entity.id for entity in self.entities]
        if len(article_ids) != len(set(article_ids)) or len(entity_ids) != len(set(entity_ids)):
            raise ValueError("archive identities must be unique")
        known = set(entity_ids)
        for article in self.articles:
            if not set(article.entity_ids).issubset(known):
                raise ValueError("article refers to an absent entity")
        return self
