"""the language interface everything else talks to.

i put all the language specific stuff behind this so the rest of the code
never has to know what it is looking at. if i want farsi later i write one
new class and nothing else in the project changes. that is P3 in the brief.

the big rule here is that normalize() output is only ever used for comparing
things. i never write it back to storage. normalizing shortens the string so
every mention offset i already recorded would point at the wrong place and
P2 would break. the original text stays exactly as it was scraped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AlignedText:
    """normalized text plus a map back to where each character came from.

    i need this because i match patterns against normalized text but a
    mention has to point at the original. those are different lengths.
    مُحَمَّد is eight characters and normalizes to four so an offset in one
    is meaningless in the other.

    source_offsets[i] is the index in the original string that produced
    normalized character i. so a match on normalized[s:e] turns into
    original[source_offsets[s] : source_offsets[e - 1] + 1].

    the end is the last character plus one instead of source_offsets[e]
    because the normalized character at e might not exist or might come
    from much further along if characters got dropped in between.
    """

    text: str
    source_offsets: tuple[int, ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """turn a span in the normalized text into a span in the original."""
        if start >= end or not self.source_offsets:
            raise ValueError(f"bad span {start}:{end}")
        return self.source_offsets[start], self.source_offsets[end - 1] + 1


@runtime_checkable
class LanguageAdapter(Protocol):
    """what every language has to be able to do.

    i used Protocol instead of an abstract base class because it is
    structural. a class counts as a LanguageAdapter just by having these
    methods and it never has to import or inherit from anything here. the
    brief already uses the word protocol for the extractor interfaces in M3
    so i stayed consistent.

    runtime_checkable lets me do isinstance(x, LanguageAdapter) in tests.
    it only checks that the method names exist and not the signatures so it
    is a weak check but it is enough to catch a totally wrong object.
    """

    code: str

    def detect(self, text: str) -> float:
        """how much of this text looks like my language. 0 to 1."""
        ...

    def normalize(self, text: str) -> str:
        """fold text into a comparison key. never store this."""
        ...

    def normalize_aligned(self, text: str) -> AlignedText:
        """same fold but it also tells me where every character came from.

        extractors need this. matching happens on the folded text and the
        mention has to land on the original or P2 breaks.
        """
        ...

    def tokenize(self, text: str) -> list[str]:
        """split into words."""
        ...

    def blocking_keys(self, name: str) -> set[str]:
        """cheap keys for M4 blocking. two names that might be the same
        thing should share at least one key."""
        ...

    def romanize(self, text: str) -> list[str]:
        """latin spellings someone might plausibly write. more than one
        because محمد is Mohammed and Muhammad and Mohamed and they are all
        the same guy."""
        ...


class AdapterRegistry:
    """holds the adapters and picks one for a chunk of text.

    i keep this tiny on purpose. it asks every adapter how much the text
    looks like theirs and takes the highest score. no language detection
    library needed because counting characters in a script range is already
    good enough when the languages use completely different alphabets.
    """

    def __init__(self, adapters: list[LanguageAdapter], default_code: str) -> None:
        self._by_code = {adapter.code: adapter for adapter in adapters}
        self._default = self._by_code[default_code]

    def get(self, code: str) -> LanguageAdapter:
        try:
            return self._by_code[code]
        except KeyError:
            raise ValueError(f"no adapter registered for language {code!r}") from None

    def codes(self) -> list[str]:
        return list(self._by_code)

    def detect(self, text: str) -> LanguageAdapter:
        """pick the adapter whose script shows up most in this text."""
        if not text:
            return self._default

        best = max(self._by_code.values(), key=lambda adapter: adapter.detect(text))
        # if nothing scored at all the text is probably digits or symbols so
        # i just hand back the default instead of picking a random winner.
        return best if best.detect(text) > 0 else self._default
