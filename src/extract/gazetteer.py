"""gazetteer extractor. the baseline everything else gets measured against.

it knows nothing. it has a list of names and it finds them. that is the
point. the brief says build this first because a number is only meaningful
next to another number and this is the other number. if the transformer in
M3 cannot beat a dictionary lookup then it is not earning its dependency.

the interesting part is not the matching. it is that matching happens on
normalized text and mentions have to come back pointing at the original.
that is what the alignment from M2 is for.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.extract.automaton import Automaton
from src.extract.base import ExtractedMention, resolve_overlaps
from src.lang.arabic import ArabicAdapter

_GAZETTEER_PATH = Path(__file__).resolve().parents[2] / "config" / "gazetteer.yaml"


class GazetteerExtractor:
    name = "gazetteer_extractor"
    # 1.0.0 to start. bumping this is what makes already extracted mentions
    # findable and reprocessable exactly like the classifier version bump did
    version = "1.0.0"

    def __init__(self, gazetteer_path: Path | None = None, adapter: ArabicAdapter | None = None) -> None:
        self._adapter = adapter or ArabicAdapter()
        self._automaton = Automaton()
        # normalized surface form -> (object_type, canonical name). the
        # gazetteer already asserts that ترامب and دونالد ترامب name the same
        # person, and resolution was throwing that away and trying to
        # rediscover it with a fuzzy scorer. this is what lets it just know.
        self._canonical_by_surface: dict[str, tuple[str, str]] = {}
        self._load(gazetteer_path or _GAZETTEER_PATH)

    def _load(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        for object_type, entries in raw.items():
            for canonical, aliases in (entries or {}).items():
                # the canonical spelling counts as one of its own surface
                # forms. i normalize every one of them because the document
                # gets normalized too and both sides have to agree
                for surface in [canonical, *(aliases or [])]:
                    key = self._adapter.normalize(surface)
                    if key:
                        self._automaton.add(key, payload=(object_type, canonical))
                        self._canonical_by_surface[key] = (object_type, canonical)

        self._automaton.build()

    def __len__(self) -> int:
        return len(self._automaton)

    def canonical_for(self, normalized_surface: str) -> tuple[str, str] | None:
        """what the gazetteer thinks this surface form names.

        returns (object_type, canonical) or None if the form is not in the
        dictionary. resolution uses this to merge known aliases without
        guessing, and falls back to the learned scorer for everything else.

        this is the same tradeoff as M3. the dictionary is exact on what it
        knows and blind to everything else, so I use it where it knows and
        the fuzzy path where it does not.
        """
        return self._canonical_by_surface.get(normalized_surface)

    def extract(self, text: str) -> list[ExtractedMention]:
        if not text:
            return []

        aligned = self._adapter.normalize_aligned(text)
        if not aligned.text:
            return []

        mentions: list[ExtractedMention] = []
        for match in self._automaton.scan(aligned.text):
            if not _is_whole_word(aligned.text, match.start, match.end):
                continue

            object_type, _canonical = match.payload
            start, end = aligned.original_span(match.start, match.end)
            mentions.append(
                ExtractedMention(
                    text=text[start:end],
                    start=start,
                    end=end,
                    object_type=object_type,
                )
            )

        return resolve_overlaps(mentions)


def _is_whole_word(text: str, start: int, end: int) -> bool:
    """make sure the match is not sitting inside a longer word.

    the normalized text is space separated so i only have to look at the one
    character on each side. without this مصر matches inside مصرف and every
    banking article grows an egypt mention.

    arabic glues و and ب and ل onto the front of words so a match that
    starts right after one of those is still a real match. i allow that
    specific case and nothing else.
    """
    before_ok = start == 0 or text[start - 1] == " " or text[start - 1] in "وفبلك"
    after_ok = end == len(text) or text[end] == " "
    return before_ok and after_ok
