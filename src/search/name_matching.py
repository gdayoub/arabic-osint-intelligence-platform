"""Pure comparison helpers shared by SQL and offline evidence search.

Names are never rewritten. The caller supplies comparison keys made by the
existing language adapters, exactly as the original entity search did.
"""

from __future__ import annotations

from typing import Literal

MatchKind = Literal["exact", "substring"]


def normalized_match(query: str, candidate: str) -> MatchKind | None:
    """Classify the existing non-empty exact-or-substring search rule."""
    if not query:
        return None
    if query == candidate:
        return "exact"
    if query in candidate:
        return "substring"
    return None
