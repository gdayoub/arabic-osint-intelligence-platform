"""turning scored pairs into clusters. one cluster becomes one entity.

union find is the obvious tool and it is also the dangerous one. it does
SINGLE LINKAGE which means A joins B's cluster if A matches ANY member. so
if بشار الأسد matches الأسد at 0.72 and الأسد matches ماهر الأسد at 0.68
then all three end up as one entity even though بشار and ماهر scored 0.11
against each other and were never a match.

one weak middle link cascades. at scale this produces a giant component
that swallows a large part of the corpus and the whole resolution is
worthless. it is the single most common way entity resolution fails.

so I do union find first because it is nearly free, then I look for clusters
that came out too big and re-cluster those with COMPLETE LINKAGE which
requires a member to match every other member and not just one. complete
linkage cannot chain because the chain link itself has to hold against the
far end.

why not complete linkage everywhere. it is O(n cubed) and union find is
effectively O(n). most clusters are two or three mentions of one name and
chaining is not a risk there. I pay the expensive algorithm only where the
cheap one produced something suspicious.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable

logger = logging.getLogger("resolve.cluster")

# a cluster bigger than this is suspicious. real people get mentioned a lot
# but they do not get mentioned under fifty different surface forms. this is
# about distinct spellings and not about mention volume.
DEFAULT_MAX_CLUSTER_SIZE = 25


class UnionFind:
    """disjoint set with path compression and union by rank.

    the two optimisations do different jobs. union by rank keeps the tree
    shallow when merging by always hanging the shorter tree off the taller
    one. path compression flattens whatever depth is left by pointing every
    node it walks past straight at the root.

    together they give effectively constant time per operation. the real
    bound is inverse ackermann which is below 5 for any input that fits in
    the universe so constant is a fair description.
    """

    __slots__ = ("_parent", "_rank")

    def __init__(self, items: Iterable[int] = ()) -> None:
        self._parent: dict[int, int] = {}
        self._rank: dict[int, int] = {}
        for item in items:
            self.add(item)

    def add(self, item: int) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0

    def find(self, item: int) -> int:
        self.add(item)
        # iterative and not recursive. a long chain would blow the python
        # stack and the iterative version is barely longer.
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # second pass points everything on the path straight at the root
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> bool:
        """returns True if this actually merged two different sets."""
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return False

        if self._rank[root_a] < self._rank[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        if self._rank[root_a] == self._rank[root_b]:
            self._rank[root_a] += 1
        return True

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for item in self._parent:
            out[self.find(item)].append(item)
        return dict(out)


@dataclass(frozen=True, slots=True)
class ClusterResult:
    clusters: list[list[int]]
    giant_components_split: int
    size_histogram: dict[int, int] = field(default_factory=dict)

    @property
    def largest(self) -> int:
        return max((len(c) for c in self.clusters), default=0)


def complete_linkage(
    members: list[int],
    similarity: Callable[[int, int], float],
    threshold: float,
) -> list[list[int]]:
    """re-cluster requiring every member to match every other member.

    this is the anti chaining pass. I start with each item alone and merge
    the two clusters whose WORST cross pair is best, but only while that
    worst pair still clears the threshold. because the merge is judged on
    the worst pair, a chain cannot form: بشار can only join a cluster that
    already contains ماهر if بشار matches ماهر directly.

    O(n cubed) in the worst case which is fine because I only ever call this
    on a cluster that already tripped the size guard.
    """
    groups = [[m] for m in members]

    while len(groups) > 1:
        best_pair = None
        best_score = threshold

        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                # the worst pair between the two groups decides. that is what
                # complete linkage means and it is what blocks chaining.
                worst = min(similarity(a, b) for a in groups[i] for b in groups[j])
                if worst >= best_score:
                    best_score = worst
                    best_pair = (i, j)

        if best_pair is None:
            break

        i, j = best_pair
        groups[i] = groups[i] + groups[j]
        del groups[j]

    return groups


def cluster_pairs(
    items: Iterable[int],
    matching_pairs: Iterable[tuple[int, int]],
    similarity: Callable[[int, int], float] | None = None,
    threshold: float = 0.6,
    max_cluster_size: int = DEFAULT_MAX_CLUSTER_SIZE,
    cannot_link_pairs: Iterable[tuple[int, int]] = (),
) -> ClusterResult:
    """union find, then split anything that came out suspiciously large.

    similarity is only needed for the split pass. if it is not supplied I
    keep the giant components as they are and log it, because silently
    returning a wrong answer is worse than returning a flagged one.
    """
    items = list(items)
    uf = UnionFind(items)
    cannot_links = {tuple(sorted(pair)) for pair in cannot_link_pairs}

    # Highest-confidence edges go first.  This makes the result deterministic
    # when a human cannot-link conflicts with an indirect chain of automatic
    # matches, and preserves the strongest supported merges.
    matching_pairs = list(matching_pairs)
    if similarity is not None:
        matching_pairs.sort(key=lambda pair: similarity(*pair), reverse=True)

    for a, b in matching_pairs:
        root_a, root_b = uf.find(a), uf.find(b)
        if root_a == root_b:
            continue

        violates_constraint = False
        for left, right in cannot_links:
            left_root, right_root = uf.find(left), uf.find(right)
            if {left_root, right_root} == {root_a, root_b}:
                violates_constraint = True
                break
        if violates_constraint:
            continue
        uf.union(a, b)

    clusters: list[list[int]] = []
    split_count = 0

    for members in uf.groups().values():
        if len(members) <= max_cluster_size:
            clusters.append(sorted(members))
            continue

        logger.warning(
            "cluster of %d exceeds max %d. single linkage probably chained through a weak pair",
            len(members),
            max_cluster_size,
        )
        if similarity is None:
            clusters.append(sorted(members))
            continue

        split_count += 1
        for group in complete_linkage(sorted(members), similarity, threshold):
            clusters.append(sorted(group))

    histogram = Counter(len(c) for c in clusters)
    return ClusterResult(
        clusters=sorted(clusters, key=len, reverse=True),
        giant_components_split=split_count,
        size_histogram=dict(sorted(histogram.items())),
    )
