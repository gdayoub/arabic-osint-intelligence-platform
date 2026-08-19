"""english adapter.

this one barely does anything and that is the point. the brief asks for a
second implementation to prove the interface actually works for more than
one language. writing it is what tells me whether i accidentally designed
LanguageAdapter around arabic.

it did catch one thing. my first cut had normalize() returning a string with
the definite article already stripped which only makes sense for arabic
because ال is glued onto the word. english the is its own token so that
belonged in blocking_keys instead. moved it and both languages got simpler.
"""

from __future__ import annotations

import re
import unicodedata

from src.lang.base import AlignedText

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_LATIN = re.compile(r"[A-Za-z]")

# words i drop from name keys because they carry no identity. arabic has the
# same idea with ال but as a prefix instead of separate words.
_NAME_NOISE = {"the", "of", "al", "el", "bin", "ibn", "van", "von", "de", "da"}


class EnglishAdapter:
    code = "en"

    def detect(self, text: str) -> float:
        letters = [ch for ch in text if ch.isalpha()]
        if not letters:
            return 0.0
        latin = sum(1 for ch in letters if _LATIN.match(ch))
        return latin / len(letters)

    def normalize(self, text: str) -> str:
        return self.normalize_aligned(text).text

    def normalize_aligned(self, text: str) -> AlignedText:
        """same shape as the arabic one. one pass keeping the source index.

        writing this is what caught that i had added normalize_aligned to
        the protocol and only implemented it on the arabic side. the tests
        went red on english which is exactly the job this class exists to
        do. one implementation would have let it through.

        NFKD splits an accented letter into a base plus a combining mark and
        then i drop the mark so café and cafe compare equal. arabic gets the
        same result from its own diacritic table.
        """
        if not text:
            return AlignedText("", ())

        chars: list[str] = []
        offsets: list[int] = []

        for index, char in enumerate(text):
            for expanded in unicodedata.normalize("NFKD", char):
                if unicodedata.combining(expanded):
                    continue

                if _PUNCT.match(expanded) or expanded.isspace():
                    if chars and chars[-1] != " ":
                        chars.append(" ")
                        offsets.append(index)
                    continue

                chars.append(expanded.lower())
                offsets.append(index)

        if chars and chars[-1] == " ":
            chars.pop()
            offsets.pop()

        return AlignedText("".join(chars), tuple(offsets))

    def tokenize(self, text: str) -> list[str]:
        normalized = self.normalize(text)
        return normalized.split() if normalized else []

    def blocking_keys(self, name: str) -> set[str]:
        tokens = [t for t in self.tokenize(name) if t not in _NAME_NOISE]
        if not tokens:
            return set()

        keys = {
            "full:" + "".join(tokens),
            "last:" + tokens[-1],
            "sorted:" + "".join(sorted(tokens)),
        }
        if len(tokens) > 1:
            keys.add("firstlast:" + tokens[0] + tokens[-1][:1])

        joined = "".join(tokens)
        for i in range(len(joined) - 2):
            keys.add("tri:" + joined[i : i + 3])
        return keys

    def romanize(self, text: str) -> list[str]:
        """english is already latin so there is nothing to convert.

        i still have to implement it because the protocol says so. a method
        that does nothing looks silly until you remember the caller does not
        know which language it is holding and should not have to check.
        """
        normalized = self.normalize(text)
        return [normalized] if normalized else []
