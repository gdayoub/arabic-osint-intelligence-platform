"""loads the hand labelled eval set and turns mention text into offsets.

i store mention text in the json instead of character offsets on purpose.
hand counting offsets in arabic is how i end up with a silently broken eval
set that reports a confident wrong number. here the loader finds each
mention and derives the offsets which means they cannot be typed wrong.

the loader also asserts each mention appears exactly once in its sentence.
if it appears twice i do not know which one the label meant so i refuse to
guess and fail loudly instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.extract.base import ExtractedMention

EVAL_PATH = Path(__file__).resolve().parents[2] / "tests" / "golden" / "ner_eval.json"


@dataclass(frozen=True, slots=True)
class EvalDocument:
    text: str
    mentions: tuple[ExtractedMention, ...]


def load_eval_set(path: Path | None = None) -> list[EvalDocument]:
    raw = json.loads((path or EVAL_PATH).read_text(encoding="utf-8"))

    documents: list[EvalDocument] = []
    for entry in raw["documents"]:
        text = entry["text"]
        mentions: list[ExtractedMention] = []

        for label in entry["mentions"]:
            surface = label["text"]
            occurrences = text.count(surface)
            if occurrences != 1:
                raise ValueError(
                    f"mention {surface!r} appears {occurrences} times in {text[:40]!r}. "
                    "the label is ambiguous so i will not guess which one it meant"
                )

            start = text.index(surface)
            mention = ExtractedMention(
                text=surface,
                start=start,
                end=start + len(surface),
                object_type=label["type"],
            )
            # P2 again. if this ever trips the eval set itself is lying and
            # every number computed from it is meaningless
            assert text[mention.start : mention.end] == mention.text
            mentions.append(mention)

        documents.append(EvalDocument(text=text, mentions=tuple(mentions)))

    return documents
