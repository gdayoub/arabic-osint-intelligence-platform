"""tests for blocking.

the two that matter are the recall ceiling one and the oversized block one.
everything else is arithmetic.
"""

from __future__ import annotations

from src.lang.arabic import ArabicAdapter
from src.resolve.blocking import KeyBlocker, pair_completeness

ar = ArabicAdapter()


def _records(names: dict[int, str]) -> dict[int, set[str]]:
    return {rid: ar.blocking_keys(name) for rid, name in names.items()}


def test_spelling_variants_land_in_the_same_block(session=None):
    """the whole point. two spellings of one name have to be compared or
    resolution never gets the chance to merge them."""
    records = _records({1: "بشار الأسد", 2: "بشار الاسد"})
    pairs = KeyBlocker().candidate_pairs(records)
    assert (1, 2) in pairs


def test_unrelated_names_are_never_compared():
    records = _records({1: "بشار الأسد", 2: "دونالد ترامب"})
    assert KeyBlocker().candidate_pairs(records) == set()


def test_reduction_ratio_is_reported():
    records = _records({i: f"اسم رقم {i}" for i in range(30)})
    blocker = KeyBlocker()
    blocker.candidate_pairs(records)
    stats = blocker.last_stats

    assert stats.total_records == 30
    assert stats.full_pairs == 30 * 29 // 2
    assert 0.0 <= stats.reduction_ratio <= 1.0


def test_oversized_blocks_are_dropped_and_recorded():
    """a key shared by everything is the quadratic problem in disguise. I
    drop it but I record what I dropped so a too coarse key type shows up
    instead of silently costing recall."""
    records = {i: {"shared:everyone"} for i in range(50)}
    blocker = KeyBlocker(max_block_size=10)
    pairs = blocker.candidate_pairs(records)

    assert pairs == set()
    assert blocker.last_stats.dropped_blocks[0] == ("shared:everyone", 50)


def test_block_at_exactly_the_cap_is_kept():
    records = {i: {"k"} for i in range(10)}
    pairs = KeyBlocker(max_block_size=10).candidate_pairs(records)
    assert len(pairs) == 10 * 9 // 2


def test_pair_completeness_catches_blocking_that_drops_true_matches():
    """reduction ratio alone would call this excellent. it is not."""
    assert pair_completeness(set(), [(1, 2), (3, 4)]) == 0.0
    assert pair_completeness({(1, 2)}, [(1, 2), (3, 4)]) == 0.5
    assert pair_completeness({(1, 2), (3, 4)}, [(1, 2), (3, 4)]) == 1.0


def test_pair_completeness_ignores_pair_ordering():
    assert pair_completeness({(1, 2)}, [(2, 1)]) == 1.0


def test_pairs_are_ordered_consistently():
    """(a, b) and (b, a) must not both appear or every pair gets scored
    twice and clustering sees phantom evidence."""
    records = _records({5: "بشار الأسد", 1: "بشار الاسد"})
    pairs = KeyBlocker().candidate_pairs(records)
    assert all(a < b for a, b in pairs)


def test_records_with_no_keys_are_harmless():
    assert KeyBlocker().candidate_pairs({1: set(), 2: set()}) == set()


def test_real_reduction_on_a_realistic_name_list():
    """the number I would actually quote. a few dozen names with the kind of
    repetition a real corpus has."""
    base = ["بشار الأسد", "دونالد ترامب", "بنيامين نتنياهو", "رجب طيب أردوغان", "علي خامنئي"]
    names = {}
    rid = 0
    for name in base:
        for variant in (name, name.replace("أ", "ا"), name.replace("إ", "ا")):
            names[rid] = variant
            rid += 1

    blocker = KeyBlocker()
    pairs = blocker.candidate_pairs(_records(names))
    stats = blocker.last_stats

    # every variant of a name must still be reachable
    assert pair_completeness(pairs, [(0, 1), (3, 4), (6, 7)]) == 1.0
    # and it should still be avoiding most of the cross name comparisons
    assert stats.reduction_ratio > 0.5
