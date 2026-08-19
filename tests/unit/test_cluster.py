"""tests for clustering.

the chaining test is the one that matters. everything else is plumbing.
"""

from __future__ import annotations

from src.resolve.cluster import UnionFind, cluster_pairs, complete_linkage


def test_union_find_basic_merging():
    uf = UnionFind([1, 2, 3])
    assert uf.find(1) != uf.find(2)
    uf.union(1, 2)
    assert uf.find(1) == uf.find(2)
    assert uf.find(3) != uf.find(1)


def test_union_returns_whether_it_actually_merged():
    uf = UnionFind([1, 2])
    assert uf.union(1, 2) is True
    assert uf.union(1, 2) is False, "already in the same set"


def test_union_find_survives_a_long_chain():
    """path compression has to be iterative. a recursive find would hit the
    python recursion limit on a chain this long."""
    uf = UnionFind()
    for i in range(5000):
        uf.union(i, i + 1)
    assert uf.find(0) == uf.find(5000)


def test_groups_partition_everything_exactly_once():
    uf = UnionFind(range(10))
    uf.union(0, 1)
    uf.union(2, 3)
    groups = uf.groups()
    assert sum(len(g) for g in groups.values()) == 10
    assert sorted(x for g in groups.values() for x in g) == list(range(10))


# ---------- the chaining problem ----------

def test_single_linkage_chains_two_different_people_together():
    """documents the danger rather than hiding it.

    بشار(1) matches الأسد(2), الأسد(2) matches ماهر(3), but بشار and ماهر
    are different people. plain union find fuses all three anyway. this test
    asserts the BAD behaviour so the guard below has something to fix.
    """
    result = cluster_pairs([1, 2, 3], [(1, 2), (2, 3)], max_cluster_size=100)
    assert result.clusters == [[1, 2, 3]], "single linkage chains, as expected"


def test_complete_linkage_refuses_to_chain():
    """same three mentions, but now a member has to match every other member.

    بشار cannot join a group containing ماهر unless بشار matches ماهر
    directly, and it does not.
    """
    scores = {(1, 2): 0.72, (2, 3): 0.68, (1, 3): 0.11}

    def similarity(a, b):
        return scores[tuple(sorted((a, b)))]

    groups = complete_linkage([1, 2, 3], similarity, threshold=0.6)
    sizes = sorted(len(g) for g in groups)

    assert sizes == [1, 2], "the weak far pair must stay split"
    assert [1, 3] not in [sorted(g) for g in groups]


def test_giant_component_gets_split_by_the_guard():
    """end to end. a chain long enough to trip the size cap gets re-clustered
    with complete linkage instead of being handed back as one blob."""
    members = list(range(10))
    chain = [(i, i + 1) for i in range(9)]

    # neighbours are similar, anything further apart is not
    def similarity(a, b):
        return 0.9 if abs(a - b) == 1 else 0.1

    result = cluster_pairs(members, chain, similarity=similarity, threshold=0.6, max_cluster_size=5)

    assert result.giant_components_split == 1
    assert result.largest <= 5, "the blob was broken up"
    assert sum(len(c) for c in result.clusters) == 10, "nothing lost or duplicated"


def test_oversized_cluster_is_kept_and_flagged_when_no_similarity_given():
    """without a similarity function I cannot re-cluster. returning the blob
    flagged beats silently returning a wrong answer."""
    result = cluster_pairs(range(10), [(i, i + 1) for i in range(9)], max_cluster_size=3)
    assert result.largest == 10
    assert result.giant_components_split == 0


# ---------- reporting ----------

def test_singletons_are_kept_as_their_own_cluster():
    """a mention that matched nothing is still an entity of one. dropping it
    would silently lose data."""
    result = cluster_pairs([1, 2, 3], [(1, 2)])
    assert sorted(len(c) for c in result.clusters) == [1, 2]


def test_size_histogram_is_reported():
    result = cluster_pairs(range(6), [(0, 1), (2, 3), (4, 5)])
    assert result.size_histogram == {2: 3}


def test_no_pairs_means_every_mention_stands_alone():
    result = cluster_pairs(range(5), [])
    assert len(result.clusters) == 5
    assert result.size_histogram == {1: 5}
