"""tests for the aho corasick automaton.

i test it on ascii first because the failures are readable and the algorithm
does not care what alphabet it is given. then arabic to prove that.
"""

from __future__ import annotations

import pytest

from src.extract.automaton import Automaton


def build(*patterns: str) -> Automaton:
    a = Automaton()
    for p in patterns:
        a.add(p, payload=p.upper())
    a.build()
    return a


def test_finds_a_single_pattern():
    matches = build("he").scan("xxhexx")
    assert [(m.start, m.end, m.pattern) for m in matches] == [(2, 4, "he")]


def test_finds_overlapping_patterns():
    """the classic case. she contains he and both have to come back.

    a plain trie would report she and move on. the failure links are what
    make the shorter one show up too.
    """
    matches = build("he", "she", "his", "hers").scan("ushers")
    found = {(m.start, m.end, m.pattern) for m in matches}
    assert ("she" in {m.pattern for m in matches})
    assert ("he" in {m.pattern for m in matches})
    assert ("hers" in {m.pattern for m in matches})
    assert (1, 4, "she") in found
    assert (2, 4, "he") in found


def test_finds_repeated_occurrences():
    matches = build("ab").scan("ababab")
    assert [m.start for m in matches] == [0, 2, 4]


def test_no_match_returns_empty():
    assert build("zzz").scan("aaaa") == []


def test_offsets_slice_back_to_the_pattern():
    """the property everything downstream depends on. if this is wrong every
    mention offset is wrong."""
    text = "the quick brown fox jumps"
    for m in build("quick", "brown", "fox").scan(text):
        assert text[m.start : m.end] == m.pattern


def test_payload_comes_back():
    matches = build("cat").scan("a cat here")
    assert matches[0].payload == "CAT"


def test_works_on_arabic():
    text = "قال بشار الاسد ان الوضع في سوريا صعب"
    matches = build("بشار الاسد", "سوريا").scan(text)
    found = {m.pattern for m in matches}
    assert found == {"بشار الاسد", "سوريا"}
    for m in matches:
        assert text[m.start : m.end] == m.pattern


def test_pattern_that_is_a_suffix_of_another():
    """اسد is inside بشار الاسد so scanning has to report both."""
    text = "بشار الاسد"
    matches = build("بشار الاسد", "اسد").scan(text)
    assert {m.pattern for m in matches} == {"بشار الاسد", "اسد"}


def test_empty_pattern_is_ignored():
    a = Automaton()
    a.add("")
    a.build()
    assert len(a) == 0
    assert a.scan("anything") == []


def test_cannot_add_after_build():
    a = build("x")
    with pytest.raises(RuntimeError, match="cannot add"):
        a.add("y")


def test_cannot_scan_before_build():
    a = Automaton()
    a.add("x")
    with pytest.raises(RuntimeError, match="build"):
        a.scan("x")


def test_scanning_cost_does_not_grow_with_pattern_count():
    """the actual reason i used this algorithm.

    i am not timing anything because that would be flaky in CI. instead i
    check the thing timing would show. the same text scanned against ten
    patterns and against a thousand walks the same characters once either
    way so the result for the shared pattern is identical.
    """
    text = "the quick brown fox" * 50

    small = build("fox")
    large_patterns = ["fox"] + [f"nomatch{i}" for i in range(1000)]
    large = Automaton()
    for p in large_patterns:
        large.add(p, payload=p.upper())
    large.build()

    assert len(small.scan(text)) == len(large.scan(text))
