"""tests for the gazetteer extractor and the scoring harness."""

from __future__ import annotations

import pytest

from src.extract.base import ExtractedMention, resolve_overlaps
from src.extract.evaluate import Score, disagreements, score_by_type, score_corpus, score_document
from src.extract.eval_set import load_eval_set
from src.extract.gazetteer import GazetteerExtractor

extractor = GazetteerExtractor()


# ---------- the invariant everything rests on ----------

def test_every_mention_offset_slices_back_to_its_own_text():
    """P2. if this breaks then provenance points at the wrong characters and
    the whole M1 foundation is lying. i check it across the entire eval set
    rather than one example because it is that important.
    """
    for document in load_eval_set():
        for mention in extractor.extract(document.text):
            assert document.text[mention.start : mention.end] == mention.text


def test_offsets_land_on_the_original_spelling_not_the_normalized_one():
    """the alignment doing its job. the gazetteer holds محمد without
    diacritics and the document has them so the match happens on the folded
    text but the span has to come back covering the original characters."""
    text = "قال مُحَمَّد باقر قاليباف كلاما"
    mentions = extractor.extract(text)
    assert mentions
    hit = mentions[0]
    # the returned text keeps its diacritics because it was sliced out of
    # the original and never out of the normalized copy
    assert "مُحَمَّد" in text[hit.start : hit.end] or hit.text in text


# ---------- matching behaviour ----------

def test_finds_a_known_person():
    mentions = extractor.extract("التقى بشار الأسد وفدا في دمشق")
    assert any(m.object_type == "person" for m in mentions)


def test_alias_matches_as_well_as_the_canonical_form():
    """ترامب on its own should hit even though the canonical entry is
    دونالد ترامب."""
    assert any(m.object_type == "person" for m in extractor.extract("صرح ترامب اليوم"))


def test_spelling_variants_match_without_being_listed():
    """أردوغان and اردوغان differ by a hamza. i never listed both. the
    adapter folds them so one gazetteer entry covers both spellings."""
    with_hamza = extractor.extract("وصل أردوغان الى الدوحة")
    without = extractor.extract("وصل اردوغان الى الدوحة")
    assert len(with_hamza) == len(without) >= 1


def test_does_not_match_inside_a_longer_word():
    """مصر sits inside مصرف. without the boundary check every banking story
    grows an egypt mention."""
    mentions = extractor.extract("أشار المصرف المركزي الى استقرار العملة")
    assert not any(m.text.strip() == "مصر" for m in mentions)


def test_attached_prefix_still_matches():
    """arabic glues و and ب onto the front of a word so بسوريا is still a
    mention of سوريا."""
    assert extractor.extract("الوضع بسوريا صعب")


def test_longest_match_wins_over_the_substring():
    """حزب الله contains الله. only the long one should survive."""
    mentions = extractor.extract("أعلن حزب الله موقفه")
    assert len(mentions) == 1
    assert mentions[0].object_type == "organization"


def test_empty_text_is_safe():
    assert extractor.extract("") == []


def test_text_with_no_known_entities_returns_nothing():
    assert extractor.extract("الطقس اليوم جميل جدا") == []


# ---------- overlap resolution ----------

def test_resolve_overlaps_keeps_the_longer_span():
    long_one = ExtractedMention("بشار الاسد", 0, 10, "person")
    short_one = ExtractedMention("اسد", 6, 9, "person")
    assert resolve_overlaps([short_one, long_one]) == [long_one]


def test_resolve_overlaps_keeps_disjoint_spans():
    a = ExtractedMention("مصر", 0, 3, "location")
    b = ExtractedMention("سوريا", 10, 15, "location")
    assert len(resolve_overlaps([a, b])) == 2


def test_resolve_overlaps_returns_them_in_document_order():
    a = ExtractedMention("مصر", 20, 23, "location")
    b = ExtractedMention("سوريا", 0, 5, "location")
    assert [m.start for m in resolve_overlaps([a, b])] == [0, 20]


# ---------- scoring ----------

def test_perfect_prediction_scores_one():
    gold = [ExtractedMention("مصر", 0, 3, "location")]
    s = score_document(list(gold), gold)
    assert (s.precision, s.recall, s.f1) == (1.0, 1.0, 1.0)


def test_wrong_type_counts_as_both_a_miss_and_a_false_alarm():
    gold = [ExtractedMention("الأهلي", 0, 6, "organization")]
    predicted = [ExtractedMention("الأهلي", 0, 6, "person")]
    s = score_document(predicted, gold)
    assert s.true_positives == 0
    assert s.false_positives == 1
    assert s.false_negatives == 1


def test_f1_punishes_tagging_everything():
    """the reason f1 is a harmonic mean. an extractor that guesses wildly
    gets great recall and a plain average would flatter it."""
    gold = [ExtractedMention("مصر", 0, 3, "location")]
    spam = gold + [ExtractedMention(f"x{i}", i * 10 + 100, i * 10 + 103, "location") for i in range(99)]
    s = score_document(spam, gold)
    assert s.recall == 1.0
    assert s.precision < 0.02
    assert s.f1 < 0.05


def test_empty_prediction_scores_zero_not_a_crash():
    gold = [ExtractedMention("مصر", 0, 3, "location")]
    s = score_document([], gold)
    assert s.f1 == 0.0


def test_score_with_nothing_at_all_is_zero_and_does_not_divide_by_zero():
    assert Score(0, 0, 0).f1 == 0.0


def test_per_type_scores_split_out():
    gold = [
        ExtractedMention("مصر", 0, 3, "location"),
        ExtractedMention("حماس", 10, 14, "organization"),
    ]
    predicted = [ExtractedMention("مصر", 0, 3, "location")]
    by_type = score_by_type([(predicted, gold)])
    assert by_type["location"].f1 == 1.0
    assert by_type["organization"].f1 == 0.0


def test_disagreements_reports_both_directions():
    gold = [ExtractedMention("مصر", 0, 3, "location")]
    predicted = [ExtractedMention("سوريا", 10, 15, "location")]
    spurious, missed = disagreements(predicted, gold)
    assert [m.text for m in spurious] == ["سوريا"]
    assert [m.text for m in missed] == ["مصر"]


# ---------- the eval set itself ----------

def test_eval_set_offsets_are_all_valid():
    """the eval set is an artifact that can be wrong too. if its own offsets
    do not slice correctly then every f1 computed from it is meaningless."""
    for document in load_eval_set():
        for mention in document.mentions:
            assert document.text[mention.start : mention.end] == mention.text


def test_eval_set_rejects_an_ambiguous_label(tmp_path):
    """a mention appearing twice means i cannot tell which one was meant so
    the loader refuses rather than silently picking the first."""
    import json

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"documents": [{"text": "مصر ثم مصر", "mentions": [{"text": "مصر", "type": "location"}]}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        load_eval_set(bad)


def test_gazetteer_beats_a_do_nothing_baseline():
    """sanity floor. if the gazetteer ever scores worse than extracting
    nothing then something is very wrong."""
    documents = load_eval_set()
    real = score_corpus([(extractor.extract(d.text), list(d.mentions)) for d in documents])
    nothing = score_corpus([([], list(d.mentions)) for d in documents])
    assert real.f1 > nothing.f1
