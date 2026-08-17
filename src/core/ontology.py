"""Loads config/ontology.yaml and exposes object/link type definitions.

This is the only module allowed to know that ontology.yaml exists. Everything
else asks an Ontology instance "is this a valid object_type / link_type"
rather than reading the file itself, keeping type validation in one place (P3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class PropertyDef:
    name: str
    type: str
    required: bool = False
    values: tuple[str, ...] | None = None  # only meaningful when type == "enum"


@dataclass(frozen=True, slots=True)
class ObjectTypeDef:
    name: str
    display_name: dict[str, str]
    properties: tuple[PropertyDef, ...]


@dataclass(frozen=True, slots=True)
class LinkTypeDef:
    name: str
    from_type: str  # "*" means any object type
    to_type: str
    symmetric: bool = False


class Ontology:
    """In-memory view of config/ontology.yaml."""

    def __init__(
        self,
        object_types: dict[str, ObjectTypeDef],
        link_types: dict[str, LinkTypeDef],
        document_attributes: dict[str, PropertyDef] | None = None,
    ) -> None:
        self._object_types = object_types
        self._link_types = link_types
        self._document_attributes = document_attributes or {}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Ontology":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        object_types = {
            name: _parse_object_type(name, definition)
            for name, definition in raw.get("object_types", {}).items()
        }
        link_types = {
            entry["name"]: _parse_link_type(entry) for entry in raw.get("link_types", [])
        }
        document_attributes = {
            prop["name"]: _parse_property(prop) for prop in raw.get("document_attributes", [])
        }
        return cls(object_types, link_types, document_attributes)

    def object_type_names(self) -> frozenset[str]:
        return frozenset(self._object_types)

    def is_valid_object_type(self, name: str) -> bool:
        return name in self._object_types

    def get_object_type(self, name: str) -> ObjectTypeDef:
        try:
            return self._object_types[name]
        except KeyError:
            raise ValueError(f"Unknown object_type {name!r}; not in ontology.yaml") from None

    def is_valid_link_type(self, link_type: str, from_object_type: str, to_object_type: str) -> bool:
        definition = self._link_types.get(link_type)
        if definition is None:
            return False
        from_ok = definition.from_type in ("*", from_object_type)
        to_ok = definition.to_type in ("*", to_object_type)
        return from_ok and to_ok

    def is_valid_document_attribute(self, name: str) -> bool:
        return name in self._document_attributes


def _parse_property(prop: dict[str, Any]) -> PropertyDef:
    return PropertyDef(
        name=prop["name"],
        type=prop["type"],
        required=prop.get("required", False),
        values=tuple(prop["values"]) if "values" in prop else None,
    )


def _parse_object_type(name: str, definition: dict[str, Any]) -> ObjectTypeDef:
    properties = tuple(_parse_property(prop) for prop in definition.get("properties", []))
    return ObjectTypeDef(name=name, display_name=definition.get("display_name", {}), properties=properties)


def _parse_link_type(entry: dict[str, Any]) -> LinkTypeDef:
    return LinkTypeDef(
        name=entry["name"],
        from_type=entry["from"],
        to_type=entry["to"],
        symmetric=entry.get("symmetric", False),
    )
