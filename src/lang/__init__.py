"""language adapters. import the registry from here instead of poking at
the individual modules so callers never hardcode a language."""

from src.lang.arabic import ArabicAdapter
from src.lang.base import AdapterRegistry, LanguageAdapter, SegmentationSpec, TextSpan
from src.lang.english import EnglishAdapter

# arabic is the default because that is what this corpus is. the point of the
# registry is that changing this line is the only edit needed if that changes.
REGISTRY = AdapterRegistry([ArabicAdapter(), EnglishAdapter()], default_code="ar")

__all__ = [
    "ArabicAdapter",
    "EnglishAdapter",
    "LanguageAdapter",
    "AdapterRegistry",
    "SegmentationSpec",
    "TextSpan",
    "REGISTRY",
]
