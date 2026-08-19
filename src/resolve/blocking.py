"""blocking. the thing that makes entity resolution finish at all.

the arithmetic. comparing every mention to every other one is N times N
minus one over two. at 4582 mentions that is 10.5 million pairs. at 100k
mentions it is 5 billion. the corpus grows linearly and the comparison
count grows quadratically so this is the piece that decides whether the
system works at scale or falls over.

blocking says only compare pairs that share a cheap key. بشار الأسد and
بشار الاسد both produce last:اسد so they land in the same bucket and get
scored. بشار الأسد and دونالد ترامب share nothing so they never get
compared. that is not a compromise. they were never going to match.

what blocking costs. it is a recall ceiling. any true pair that shares no
key is invisible to everything downstream and no amount of clever scoring
gets it back. that is why I use several key types instead of one. a name
missed by the last token key might still be caught by trigrams.

the reduction ratio is the number I report. pairs generated over pairs
avoided. it is meaningless on its own though. dropping every pair gives a
perfect reduction ratio and finds nothing. it has to be read next to pair
completeness which is how many TRUE pairs survived blocking. both numbers
or neither.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Protocol

logger = logging.getLogger("resolve.blocking")

# a key held by more than this many mentions produces more pairs than it is
# worth. "last:اسد" is useful. a key every single mention shares is just the
# quadratic problem wearing a disguise.
DEFAULT_MAX_BLOCK_SIZE = 100


@dataclass(frozen=True, slots=True)
class BlockingStats:
    total_records: int
    candidate_pairs: int
    full_pairs: int
    dropped_blocks: list[tuple[str, int]] = field(default_factory=list)

    @property
    def reduction_ratio(self) -> float:
        """fraction of all possible pairs I did NOT have to score.

        0.98 means 98 percent avoided. read it with pair completeness or it
        tells you nothing.
        """
        if not self.full_pairs:
            return 0.0
        return 1.0 - (self.candidate_pairs / self.full_pairs)


class Blocker(Protocol):
    name: str

    def candidate_pairs(self, records: dict[int, set[str]]) -> set[tuple[int, int]]:
        ...


class KeyBlocker:
    """standard blocking. bucket by shared key then pair within buckets.

    records maps a record id to its set of blocking keys. the keys come from
    the language adapter so this class stays language agnostic. it never
    looks at a name.
    """

    name = "key_blocker"

    def __init__(self, max_block_size: int = DEFAULT_MAX_BLOCK_SIZE) -> None:
        self._max_block_size = max_block_size
        self.last_stats: BlockingStats | None = None

    def candidate_pairs(self, records: dict[int, set[str]]) -> set[tuple[int, int]]:
        buckets: dict[str, list[int]] = defaultdict(list)
        for record_id, keys in records.items():
            for key in keys:
                buckets[key].append(record_id)

        pairs: set[tuple[int, int]] = set()
        dropped: list[tuple[str, int]] = []

        for key, members in buckets.items():
            if len(members) < 2:
                continue
            if len(members) > self._max_block_size:
                # I log these rather than silently skipping. an oversized
                # block usually means a key type is too coarse and that is
                # worth knowing about instead of hiding.
                dropped.append((key, len(members)))
                continue
            for a, b in combinations(sorted(members), 2):
                pairs.add((a, b))

        n = len(records)
        self.last_stats = BlockingStats(
            total_records=n,
            candidate_pairs=len(pairs),
            full_pairs=n * (n - 1) // 2,
            dropped_blocks=sorted(dropped, key=lambda kv: kv[1], reverse=True)[:20],
        )
        if dropped:
            logger.info("dropped %d oversized blocks. largest %s", len(dropped), dropped[:3])
        return pairs


def pair_completeness(
    candidate_pairs: set[tuple[int, int]], true_pairs: Iterable[tuple[int, int]]
) -> float:
    """fraction of genuinely matching pairs that survived blocking.

    this is the number that stops reduction ratio from lying. blocking that
    throws away 99.9 percent of pairs looks great until you notice it threw
    away half the real matches with them.
    """
    true_set = {tuple(sorted(p)) for p in true_pairs}
    if not true_set:
        return 1.0
    survived = sum(1 for p in true_set if p in candidate_pairs)
    return survived / len(true_set)
