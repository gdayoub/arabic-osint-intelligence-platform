"""the extractor interface.

every extractor takes text and hands back spans. it has a name and a version
because P4 says so and because the whole point of M3 is comparing two of
them against each other. if i cannot tell which extractor produced a mention
i cannot tell which one is better.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExtractedMention:
    """a span the extractor thinks names something.

    start and end index into the ORIGINAL text i was handed. not into any
    normalized version of it. callers rely on text[start:end] == text_
    holding and P2 dies if it does not.
    """

    text: str
    start: int
    end: int
    object_type: str
    confidence: float = 1.0

    def overlaps(self, other: ExtractedMention) -> bool:
        return self.start < other.end and other.start < self.end


@runtime_checkable
class MentionExtractor(Protocol):
    name: str
    version: str

    def extract(self, text: str) -> list[ExtractedMention]:
        ...


def resolve_overlaps(mentions: list[ExtractedMention]) -> list[ExtractedMention]:
    """longest span wins when two mentions cover the same characters.

    the gazetteer finds بشار الاسد and also finds اسد sitting inside it.
    both are real matches but as a mention the long one is the useful one so
    i keep it and drop anything it swallows.

    sorting by length descending then greedily keeping whatever does not
    collide with something already kept. the greedy pass is fine because
    dropping a shorter span never lets a longer one in later.
    """
    ordered = sorted(mentions, key=lambda m: (m.end - m.start, -m.start), reverse=True)
    kept: list[ExtractedMention] = []
    for mention in ordered:
        if not any(mention.overlaps(k) for k in kept):
            kept.append(mention)
    return sorted(kept, key=lambda m: m.start)
