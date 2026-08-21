"""arabic language adapter.

everything in here exists because arabic gets written a lot of different
ways for the same word and i need those to compare equal.

the whole file only ever produces comparison keys. nothing here touches
stored text. see the note in base.py for why that matters.
"""

from __future__ import annotations

import re
import unicodedata

from src.lang.base import AlignedText, SegmentationSpec, TextSpan
from src.lang.segmentation import segment_explicit_paragraphs, segment_sentences

# tatweel is a stretchy character people stick in the middle of words to make
# them look nice. it carries zero meaning so i drop it.
TATWEEL = "ـ"

# the little marks above and below arabic letters. they are optional and most
# news writing leaves them out so the same word shows up both ways and i have
# to strip them or nothing matches.
DIACRITICS = (
    "".join(chr(c) for c in range(0x0610, 0x061B))
    + "".join(chr(c) for c in range(0x064B, 0x0660))
    + "ٰ"
    + "".join(chr(c) for c in range(0x06D6, 0x06EE))
)

# str.maketrans builds one lookup table and translate does a single pass over
# the string. i used to chain .replace() calls which walks the whole string
# once per replacement. this is one walk total and it reads better.
_DELETE_TABLE = str.maketrans("", "", TATWEEL + DIACRITICS)

# all the letters that people swap around without meaning anything different.
_FOLD_TABLE = str.maketrans(
    {
        # four ways to write alef and writers pick whichever. i flatten them.
        "أ": "ا",  # alef with hamza above
        "إ": "ا",  # alef with hamza below
        "آ": "ا",  # alef madda
        "ٱ": "ا",  # alef wasla
        # alef maksura looks like ya without dots and shows up at the end of
        # names like مصطفى which people also write مصطفي
        "ى": "ي",
        # teh marbuta is the feminine ending. فاطمة and فاطمه are the same name
        "ة": "ه",
        # hamza sitting on a carrier letter. i collapse to the carrier
        "ؤ": "و",  # hamza on waw
        "ئ": "ي",  # hamza on ya
        "ء": "",  # bare hamza just goes away
        # arabic uses its own digit shapes sometimes
        "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
        "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    }
)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# the aligned pass walks one character at a time so it wants plain lookups
# instead of the translate tables. same data either way.
_DELETE_SET = frozenset(TATWEEL + DIACRITICS)
_FOLD_MAP = {
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي", "ء": "",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
}

# ال is the arabic the. it sticks onto the front of the word instead of being
# its own word. for names i strip it because الأسد and أسد are the same family
DEFINITE_ARTICLE = "ال"

_ARABIC_BLOCK = re.compile(r"[؀-ۿ]")

# These are deliberately small rule sets, not a claim of full Arabic NLP.
# M5 evidence needs deterministic boundaries now; a future behavior change
# receives a new SegmentationSpec version instead of rewriting old meaning.
_ARABIC_SENTENCE_MARKS = frozenset(".!؟!…")
_ARABIC_TITLE_ABBREVIATIONS = frozenset({"د", "أ", "م", "ص", "ج"})

# rough letter to latin map. one option each so i get a base spelling and then
# i vary the vowels after. this is deliberately basic. M5 is where romanization
# gets done properly with real candidate generation.
_ROMAN = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j",
    "ح": "h", "خ": "kh", "د": "d", "ذ": "dh", "ر": "r",
    "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d",
    "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f",
    "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "و": "w", "ي": "y",
}

# names that everyone spells a bunch of different ways in english. a lookup is
# honest here because these are the ones that actually matter and guessing
# them from letters alone does not work.
_KNOWN_ROMANIZATIONS = {
    "محمد": ["mohammed", "muhammad", "mohamed", "mohammad"],
    "احمد": ["ahmed", "ahmad"],
    "علي": ["ali", "aly"],
    "حسين": ["hussein", "husayn", "hussain"],
    "حسن": ["hassan", "hasan"],
    "عبدالله": ["abdullah", "abdallah", "abdulla"],
    "ابراهيم": ["ibrahim", "ebrahim"],
    "يوسف": ["youssef", "yousef", "yusuf"],
    "خالد": ["khaled", "khalid"],
    "عمر": ["omar", "umar"],
}


class ArabicAdapter:
    code = "ar"
    segmentation = SegmentationSpec(name="arabic_rule_segmenter", version="1.0.0")

    def detect(self, text: str) -> float:
        """fraction of the letters that sit in the arabic unicode block.

        i only count letters. counting spaces and digits would drag the score
        down on every text and make the comparison useless.
        """
        letters = [ch for ch in text if ch.isalpha()]
        if not letters:
            return 0.0
        arabic = sum(1 for ch in letters if _ARABIC_BLOCK.match(ch))
        return arabic / len(letters)

    def normalize(self, text: str) -> str:
        """fold text down to something i can compare.

        this just throws away the alignment. i route it through the aligned
        version so the two can never drift apart and disagree about what a
        normalized string looks like.
        """
        return self.normalize_aligned(text).text

    def normalize_aligned(self, text: str) -> AlignedText:
        """fold the text and remember where every surviving character came from.

        the whole thing is one pass over the original characters instead of
        the chained translate calls normalize used to do. i have to go
        character by character here because that is the only way to know
        which source index produced which output character.

        NFKC gets applied per character rather than to the whole string.
        that is not identical to NFKC on the full string because real NFKC
        can compose across character boundaries. it does not matter here
        because the sequences that would compose are combining marks and i
        delete every one of those anyway.
        """
        if not text:
            return AlignedText("", ())

        chars: list[str] = []
        offsets: list[int] = []

        for index, char in enumerate(text):
            # NFKC unpacks ligatures and presentation forms. arabic has a
            # single codepoint for whole words like ﷲ and separate codepoints
            # for the same letter depending where it sits in a word. one
            # source character can expand into several output ones and they
            # all point back at the same original index.
            for expanded in unicodedata.normalize("NFKC", char):
                if expanded in _DELETE_SET:
                    continue

                folded = _FOLD_MAP.get(expanded, expanded)
                if not folded:
                    # bare hamza folds to nothing so there is no output
                    # character and nothing to record an offset for
                    continue

                if _PUNCT.match(folded) or folded.isspace():
                    # punctuation and whitespace both become a single space.
                    # i skip it if the last thing i wrote was already a space
                    # so runs collapse without needing a second pass
                    if chars and chars[-1] != " ":
                        chars.append(" ")
                        offsets.append(index)
                    continue

                chars.append(folded.lower())
                offsets.append(index)

        # strip a trailing space. there can only ever be one because of the
        # collapse above. leading spaces get skipped by the same check since
        # chars is empty at the start.
        if chars and chars[-1] == " ":
            chars.pop()
            offsets.pop()

        return AlignedText("".join(chars), tuple(offsets))

    def tokenize(self, text: str) -> list[str]:
        normalized = self.normalize(text)
        return normalized.split() if normalized else []

    def strip_article(self, token: str) -> str:
        """drop the leading ال off a word.

        the guard is on what is left over and not on the input length. my
        first version checked the input and happily turned الله into له which
        is a completely different word. i need at least three letters
        remaining for the stump to still be a real word.

        erring toward not stripping is the safe direction. leaving the ال on
        means a name might fail to match its bare form which costs me a match.
        stripping too eagerly invents matches that are wrong.
        """
        if not token.startswith(DEFINITE_ARTICLE):
            return token
        remainder = token[len(DEFINITE_ARTICLE) :]
        return remainder if len(remainder) >= 3 else token

    def blocking_keys(self, name: str) -> set[str]:
        """cheap keys so M4 can avoid comparing every name to every other one.

        two spellings of the same person need to collide on at least one key
        or the pair never even gets scored. i prefix each key with its type so
        a family name cannot accidentally match a trigram.
        """
        tokens = [self.strip_article(t) for t in self.tokenize(name)]
        tokens = [t for t in tokens if t]
        if not tokens:
            return set()

        keys = {
            # the whole thing joined up. catches exact matches after folding
            "full:" + "".join(tokens),
            # last token is usually the family name
            "last:" + tokens[-1],
            # same tokens in a different order still land here
            "sorted:" + "".join(sorted(tokens)),
        }

        if len(tokens) > 1:
            keys.add("firstlast:" + tokens[0] + tokens[-1][:1])

        # character trigrams catch typos and dropped letters that all the
        # token level keys miss
        joined = "".join(tokens)
        for i in range(len(joined) - 2):
            keys.add("tri:" + joined[i : i + 3])

        return keys

    def romanize(self, text: str) -> list[str]:
        """latin spellings someone might reasonably write.

        i check the known list first because the common names are exactly the
        ones where letter by letter guessing gives you something nobody writes.
        """
        normalized = self.normalize(text)
        if not normalized:
            return []

        out: list[str] = []
        for token in normalized.split():
            bare = self.strip_article(token)
            if bare in _KNOWN_ROMANIZATIONS:
                out.extend(_KNOWN_ROMANIZATIONS[bare])
            else:
                out.append("".join(_ROMAN.get(ch, ch) for ch in bare))

        # dict.fromkeys keeps the first occurrence and drops later duplicates
        # and unlike set() it does not scramble the order
        return list(dict.fromkeys(candidate for candidate in out if candidate))

    def segment_sentences(self, text: str) -> list[TextSpan]:
        """return exact original-text sentences using Arabic punctuation."""
        return segment_sentences(
            text,
            terminal_marks=_ARABIC_SENTENCE_MARKS,
            is_terminal=_is_arabic_sentence_terminal,
        )

    def segment_paragraphs(self, text: str) -> list[TextSpan]:
        """return only blank-line paragraphs already present in the source."""
        return segment_explicit_paragraphs(text)


def _is_arabic_sentence_terminal(text: str, index: int) -> bool:
    """recognize a terminal mark without mistaking decimals/titles for one."""
    char = text[index]
    if char in {"!", "؟", "…"}:
        return True
    if char != ".":
        return False

    # Let the first dot consume a full ellipsis.  Later dots in that same run
    # are never reached because the shared helper advances past all of them.
    if index + 1 < len(text) and text[index + 1] == ".":
        return True
    if index > 0 and text[index - 1] == ".":
        return False
    if _is_decimal_point(text, index):
        return False
    if index + 1 < len(text) and text[index + 1].isalnum():
        return False

    previous = _previous_token(text, index)
    return previous not in _ARABIC_TITLE_ABBREVIATIONS


def _is_decimal_point(text: str, index: int) -> bool:
    return (
        index > 0
        and index + 1 < len(text)
        and text[index - 1].isdigit()
        and text[index + 1].isdigit()
    )


def _previous_token(text: str, index: int) -> str:
    start = index
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return text[start:index]
