"""features for deciding whether two mentions are the same thing.

each function turns a pair into one number between 0 and 1. the scorer
combines them with learned weights. keeping them separate and dumb means I
can look at any single feature and say what it measures which matters when
a merge goes wrong and I have to explain why.

no feature here decides anything on its own. that is deliberate. string
similarity alone merges حسن and حسين which are different people one letter
apart. context is what tells them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from src.lang.base import LanguageAdapter


def jaro_winkler(a: str, b: str) -> float:
    """string similarity tuned for names.

    plain edit distance treats a difference at the start the same as one at
    the end. for names that is wrong. محمد and احمد differ in one leading
    letter and are different people, while محمود and محمو differ at the end
    and are probably a truncation of the same name. jaro winkler boosts
    matches that share a prefix which encodes exactly that intuition.

    I wrote it out because it is thirty lines and the alternative was
    another dependency for one function.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    # characters can only match if they are within this window of each other
    window = max(len(a), len(b)) // 2 - 1
    window = max(window, 0)

    a_matched = [False] * len(a)
    b_matched = [False] * len(b)
    matches = 0

    for i, ch in enumerate(a):
        lo = max(0, i - window)
        hi = min(i + window + 1, len(b))
        for j in range(lo, hi):
            if not b_matched[j] and b[j] == ch:
                a_matched[i] = b_matched[j] = True
                matches += 1
                break

    if not matches:
        return 0.0

    # transpositions are matched characters that appear in a different order
    transpositions = 0
    k = 0
    for i, flag in enumerate(a_matched):
        if not flag:
            continue
        while not b_matched[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1
    transpositions //= 2

    jaro = (
        matches / len(a) + matches / len(b) + (matches - transpositions) / matches
    ) / 3

    # winkler bonus for a shared prefix up to four characters
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1

    return jaro + prefix * 0.1 * (1 - jaro)


def jaccard(a: set, b: set) -> float:
    """overlap of two sets. size of intersection over size of union."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


@dataclass(frozen=True, slots=True)
class MentionContext:
    """everything about a mention that a feature might want.

    this is what the scorer sees. it deliberately does not include the raw
    document text. a feature that needs to re-read the article is doing too
    much work to run on millions of pairs.
    """

    mention_id: int
    normalized_name: str
    object_type: str
    document_id: int
    source: str
    published_at: datetime | None
    blocking_keys: frozenset[str]
    # normalized names of every other mention in the same document. this is
    # the co-occurrence signal. two mentions of الأسد surrounded by the same
    # cast of characters are more likely the same person.
    co_mentions: frozenset[str]


@dataclass(frozen=True, slots=True)
class PairFeatures:
    """the feature vector. field order IS the vector order.

    I use a dataclass instead of a bare list so a weight can never silently
    line up with the wrong feature after someone reorders something.
    """

    name_similarity: float
    key_overlap: float
    co_mention_overlap: float
    temporal_proximity: float
    same_source: float
    same_type: float

    def as_vector(self) -> list[float]:
        return [getattr(self, f.name) for f in fields(self)]

    @staticmethod
    def names() -> list[str]:
        return [f.name for f in fields(PairFeatures)]


# a month. two articles about the same person are usually closer together
# than this and the decay makes anything beyond it contribute nearly zero.
_TEMPORAL_SCALE_DAYS = 30.0


def compute_features(
    a: MentionContext, b: MentionContext, adapter: LanguageAdapter | None = None
) -> PairFeatures:
    if a.published_at and b.published_at:
        gap_days = abs((a.published_at - b.published_at).total_seconds()) / 86400.0
        # linear decay to zero at the scale. I tried an exponential first and
        # it made everything past a week look identical which threw away the
        # difference between two days apart and three weeks apart.
        temporal = max(0.0, 1.0 - gap_days / _TEMPORAL_SCALE_DAYS)
    else:
        # unknown is not the same as far apart. 0.5 says no information
        # rather than pretending the dates are distant.
        temporal = 0.5

    return PairFeatures(
        name_similarity=jaro_winkler(a.normalized_name, b.normalized_name),
        key_overlap=jaccard(set(a.blocking_keys), set(b.blocking_keys)),
        co_mention_overlap=jaccard(set(a.co_mentions), set(b.co_mentions)),
        temporal_proximity=temporal,
        # same source is weak evidence and cuts both ways. one outlet writing
        # a name twice usually means the same person, but it also means the
        # feature cannot distinguish two different people that outlet covers.
        same_source=1.0 if a.source == b.source else 0.0,
        same_type=1.0 if a.object_type == b.object_type else 0.0,
    )
