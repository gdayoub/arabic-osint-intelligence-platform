"""tests for the language adapters.

three kinds of test in here and they catch different things.

the example tests check the specific arabic rules i wrote on purpose.
the golden file tests check real name pairs i labelled by hand.
the hypothesis tests throw random unicode at normalize and check the
properties hold no matter what. that last one is the only one that finds
the strings i never would have thought to write down.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.lang import REGISTRY, ArabicAdapter, EnglishAdapter, LanguageAdapter

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "arabic_name_pairs.json"

ar = ArabicAdapter()
en = EnglishAdapter()


# ---------- the arabic rules ----------

@pytest.mark.parametrize(
    "raw expected".split(),
    [
        ("مُحَمَّد", "محمد"),
        ("مـحـمـد", "محمد"),
        ("إبراهيم", "ابراهيم"),
        ("أحمد", "احمد"),
        ("آمال", "امال"),
        ("مصطفى", "مصطفي"),
        ("فاطمة", "فاطمه"),
        ("رؤوف", "رووف"),
        ("١٢٣", "123"),
    ],
)
def test_each_normalization_rule(raw, expected):
    assert ar.normalize(raw) == expected


def test_normalize_does_not_touch_the_input():
    """paranoid check. normalize returns a new string and python strings are
    immutable anyway but i want the test on record because the whole design
    rests on stored text never changing."""
    original = "مُحَمَّد"
    ar.normalize(original)
    assert original == "مُحَمَّد"


def test_article_stripping_leaves_short_words_alone():
    assert ar.strip_article("الاسد") == "اسد"
    # الله is too short to chop. stripping it would leave له which is a
    # different word entirely
    assert ar.strip_article("الله") == "الله"


# ---------- the golden pairs ----------

def _load_golden():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _load_pairs():
    return _load_golden()["pairs"]


def _load_known_limitations():
    return _load_golden()["known_limitations"]


@pytest.mark.parametrize("pair", _load_pairs(), ids=lambda p: f"{p['a']}~{p['b']}")
def test_hand_labelled_name_pairs(pair):
    """same=true pairs must fold together. same=false pairs must not.

    the false ones are the ones that matter. it is easy to write
    normalization that makes everything match everything.
    """
    a = ar.normalize(pair["a"])
    b = ar.normalize(pair["b"])
    if pair["same"]:
        assert a == b, f"should have matched. {pair['why']}"
    else:
        assert a != b, f"should NOT have matched. {pair['why']}"


@pytest.mark.xfail(strict=True, reason="known normalization gap. see known_limitations in the golden file")
@pytest.mark.parametrize("pair", _load_known_limitations(), ids=lambda p: f"{p['a']}~{p['b']}")
def test_known_limitations_still_fail(pair):
    """pairs i know are the same name but my folding does not catch.

    strict=True is the important bit. if one of these ever starts passing
    pytest fails the run instead of quietly going green. that way improving
    normalization forces me to come back and move the pair into the real
    set rather than leaving a stale note behind.
    """
    assert ar.normalize(pair["a"]) == ar.normalize(pair["b"])


# ---------- properties that hold for any input ----------

@settings(max_examples=300)
@given(st.text())
def test_normalize_is_idempotent(text):
    """running normalize twice gives the same answer as running it once.

    this is the property the brief asks for. if it failed it would mean
    normalize can keep changing its own output which makes any stored
    comparison key unstable.
    """
    once = ar.normalize(text)
    assert ar.normalize(once) == once


def test_normalize_is_idempotent_when_lowercase_expands_unicode():
    """İ.lower() adds a combining dot, which must not destabilize a key.

    This also checks the aligned form still has exactly one original offset
    for the one surviving normalized character.
    """
    aligned = ar.normalize_aligned("İ")

    assert aligned.text == "i"
    assert aligned.source_offsets == (0,)
    assert aligned.original_span(0, 1) == (0, 1)
    assert ar.normalize(aligned.text) == aligned.text


@settings(max_examples=300)
@given(st.text())
def test_normalize_never_returns_leading_or_trailing_space(text):
    result = ar.normalize(text)
    assert result == result.strip()


@settings(max_examples=200)
@given(st.text())
def test_tokenize_matches_normalize(text):
    """tokens joined back with single spaces should equal the normalized
    string. keeps the two from drifting apart."""
    assert " ".join(ar.tokenize(text)) == ar.normalize(text)


@settings(max_examples=200)
@given(st.text(min_size=1))
def test_blocking_keys_are_stable(text):
    """same input gives the same keys every time. M4 blocking would be
    nonsense otherwise."""
    assert ar.blocking_keys(text) == ar.blocking_keys(text)


# ---------- the abstraction actually holds ----------

@pytest.mark.parametrize("adapter", [ar, en], ids=["arabic", "english"])
def test_both_adapters_satisfy_the_protocol(adapter):
    assert isinstance(adapter, LanguageAdapter)


@pytest.mark.parametrize("adapter", [ar, en], ids=["arabic", "english"])
def test_every_adapter_handles_empty_input(adapter):
    assert adapter.normalize("") == ""
    assert adapter.tokenize("") == []
    assert adapter.blocking_keys("") == set()
    assert adapter.romanize("") == []
    assert adapter.detect("") == 0.0


def test_english_normalization_strips_accents():
    assert en.normalize("Café") == "cafe"
    assert en.normalize("  Hello,  World! ") == "hello world"


def test_english_drops_name_noise_from_keys():
    keys = en.blocking_keys("Bashar al Assad")
    assert "last:assad" in keys
    assert not any(k.endswith(":al") for k in keys)


# ---------- picking the right adapter ----------

def test_registry_detects_arabic_and_english():
    assert REGISTRY.detect("أعلن الرئيس عن سياسة جديدة").code == "ar"
    assert REGISTRY.detect("The president announced a new policy").code == "en"


def test_registry_falls_back_to_default_when_there_is_nothing_to_go_on():
    assert REGISTRY.detect("12345 !!! ???").code == "ar"
    assert REGISTRY.detect("").code == "ar"


def test_registry_rejects_an_unknown_language():
    with pytest.raises(ValueError, match="no adapter registered"):
        REGISTRY.get("fa")


# ---------- romanization ----------

def test_common_names_get_their_real_spellings():
    out = ar.romanize("محمد")
    assert "mohammed" in out
    assert "muhammad" in out


def test_unknown_names_fall_back_to_letter_mapping():
    out = ar.romanize("زيلينسكي")
    assert out and all(c.isascii() for c in out[0])


def test_romanize_deduplicates():
    out = ar.romanize("محمد محمد")
    assert len(out) == len(set(out))


def test_romanize_generates_multiple_candidates_for_ambiguous_consonants():
    # قطر has one letter (ق) with two common renderings; short vowels are
    # never written in the script so these keys stay vowel-less on purpose
    # (see the module docstring: comparison keys, not display spellings).
    out = ar.romanize("قطر")
    assert "qtr" in out
    assert "ktr" in out
    assert len(out) >= 2


def test_romanize_combines_multi_word_names():
    # both tokens are known lookups (الأحمد strips its article down to
    # احمد), and the combined candidates should cross the two, not just
    # vary the first word.
    out = ar.romanize("محمد الأحمد")
    assert "mohammed ahmed" in out
    assert "muhammad ahmad" in out


def test_romanize_caps_candidate_count():
    # a name packed with several ambiguous letters must not explode past the
    # search-key budget.
    out = ar.romanize("عبدالغفار الذعذاع")
    assert 0 < len(out) <= 12
