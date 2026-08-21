"""golden and property tests for source-text segmentation.

the important assertion is not just the visible sentence strings.  Every
span must still slice exactly out of the unmodified original text, because a
later evidence object will reuse these offsets next to a Mention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.lang import REGISTRY, ArabicAdapter, EnglishAdapter, LanguageAdapter, SegmentationSpec, TextSpan

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "segmentation.json"


def _load_golden() -> dict[str, list[dict[str, object]]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _actual(spans: list[TextSpan]) -> list[dict[str, object]]:
    return [{"start": span.start, "end": span.end, "text": span.text} for span in spans]


def _assert_source_aligned(text: str, spans: list[TextSpan]) -> None:
    previous_end = 0
    for span in spans:
        span.assert_matches(text)
        assert text[span.start : span.end] == span.text
        assert span.start >= previous_end
        assert text[previous_end : span.start].isspace() or span.start == previous_end
        previous_end = span.end


@pytest.mark.parametrize("case", _load_golden()["sentence_cases"], ids=lambda case: str(case["id"]))
def test_sentence_segmentation_matches_hand_checked_golden_offsets(case: dict[str, object]):
    text = str(case["text"])
    adapter = REGISTRY.get(str(case["language"]))

    actual = adapter.segment_sentences(text)

    assert _actual(actual) == case["spans"]
    _assert_source_aligned(text, actual)


@pytest.mark.parametrize("case", _load_golden()["paragraph_cases"], ids=lambda case: str(case["id"]))
def test_paragraph_segmentation_only_preserves_explicit_source_boundaries(case: dict[str, object]):
    text = str(case["text"])
    adapter = REGISTRY.get(str(case["language"]))

    actual = adapter.segment_paragraphs(text)

    assert _actual(actual) == case["spans"]
    _assert_source_aligned(text, actual)


def test_text_span_factory_and_match_check_protect_original_offsets():
    source = "قبل مُحَمَّد. بعد"
    start = source.index("مُحَمَّد")
    span = TextSpan.from_source(source, start, start + len("مُحَمَّد"))

    assert span.text == "مُحَمَّد"
    span.assert_matches(source)

    with pytest.raises(ValueError, match="outside source"):
        TextSpan.from_source(source, 0, len(source) + 1)
    with pytest.raises(ValueError, match="before start"):
        TextSpan(source, start=3, end=2)
    with pytest.raises(ValueError, match="does not match"):
        span.assert_matches("قبل محمد. بعد")


@pytest.mark.parametrize("adapter", [ArabicAdapter(), EnglishAdapter()], ids=["arabic", "english"])
def test_adapters_publish_a_versioned_segmentation_contract(adapter: LanguageAdapter):
    assert isinstance(adapter, LanguageAdapter)
    assert adapter.segmentation.name.endswith("_rule_segmenter")
    assert adapter.segmentation.version == "1.0.0"


def test_segmentation_spec_rejects_an_unversioned_identity():
    with pytest.raises(ValueError, match="name cannot be empty"):
        SegmentationSpec(name="", version="1.0.0")
    with pytest.raises(ValueError, match="version cannot be empty"):
        SegmentationSpec(name="test", version="")


def test_sentence_segmentation_keeps_raw_diacritics_instead_of_normalizing_text():
    source = "مُحَمَّد وصل إلى بيروت."

    span = ArabicAdapter().segment_sentences(source)[0]

    assert span.text == source
    assert "ُ" in span.text
    assert ArabicAdapter().normalize(span.text) != span.text


@settings(max_examples=200)
@given(st.text())
def test_every_returned_span_is_an_ordered_exact_source_slice_for_any_unicode(text: str):
    for adapter in (ArabicAdapter(), EnglishAdapter()):
        _assert_source_aligned(text, adapter.segment_sentences(text))
        _assert_source_aligned(text, adapter.segment_paragraphs(text))
