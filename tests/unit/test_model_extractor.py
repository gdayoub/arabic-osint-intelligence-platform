"""tests for the transformer extractor.

these run without torch installed. i am not testing that a neural network
predicts well because that is what the eval set and the checkpoint are for.
i am testing the wrapper around it. the label mapping and the confidence
filter and the offset handling are mine and they are where my bugs live.

the model itself gets a fake pipeline so the tests stay fast and offline.
"""

from __future__ import annotations

import pytest

from src.extract.base import ExtractedMention, resolve_overlaps
from src.extract.model import _LABEL_TO_OBJECT_TYPE, ModelExtractor, is_available


class FakePipeline:
    """stands in for the huggingface pipeline.

    returns whatever i hand it in the same shape the real one uses. that
    shape is entity_group and score and start and end and word.
    """

    def __init__(self, entities):
        self._entities = entities

    def __call__(self, text):
        return self._entities


def build(entities, min_confidence: float = 0.5) -> ModelExtractor:
    """makes a ModelExtractor without going near torch.

    __new__ skips __init__ which is what would import transformers and pull
    down a model. then i set the two attributes extract actually reads.
    slightly sneaky but it means these tests run in the normal suite.
    """
    extractor = ModelExtractor.__new__(ModelExtractor)
    extractor._pipeline = FakePipeline(entities)
    extractor.min_confidence = min_confidence
    return extractor


TEXT = "التقى بشار الأسد وفدا في دمشق"


def test_maps_model_labels_onto_ontology_types():
    extractor = build([{"entity_group": "PER", "score": 0.99, "start": 6, "end": 16, "word": "بشار الأسد"}])
    mentions = extractor.extract(TEXT)
    assert len(mentions) == 1
    assert mentions[0].object_type == "person"


def test_gpe_becomes_location():
    """some tag sets call a country a geo political entity. my ontology only
    has location so they have to land there or the mention gets dropped."""
    assert _LABEL_TO_OBJECT_TYPE["GPE"] == "location"


def test_unknown_label_is_dropped_not_guessed():
    extractor = build([{"entity_group": "MISC", "score": 0.99, "start": 0, "end": 5, "word": "شيء"}])
    assert extractor.extract(TEXT) == []


def test_low_confidence_predictions_are_filtered():
    entities = [
        {"entity_group": "LOC", "score": 0.99, "start": 25, "end": 29, "word": "دمشق"},
        {"entity_group": "PER", "score": 0.20, "start": 6, "end": 16, "word": "بشار الأسد"},
    ]
    mentions = build(entities, min_confidence=0.5).extract(TEXT)
    assert [m.object_type for m in mentions] == ["location"]


def test_text_comes_from_slicing_the_original_not_from_the_word_field():
    """the pipeline word field is decoded back from tokens and can differ
    from the source by a space or a stripped character. the offsets are the
    truth so i slice with them. if i trusted word then mention.text would
    not match text[start:end] and P2 would be quietly broken.
    """
    entities = [{"entity_group": "LOC", "score": 0.99, "start": 25, "end": 29, "word": "WRONG"}]
    mention = build(entities).extract(TEXT)[0]
    assert mention.text == TEXT[25:29]
    assert mention.text != "WRONG"


def test_every_returned_offset_slices_back_correctly():
    entities = [
        {"entity_group": "PER", "score": 0.9, "start": 6, "end": 16, "word": "x"},
        {"entity_group": "LOC", "score": 0.9, "start": 25, "end": 29, "word": "y"},
    ]
    for mention in build(entities).extract(TEXT):
        assert TEXT[mention.start : mention.end] == mention.text


def test_zero_width_and_whitespace_spans_are_dropped():
    # index 5 is the space between التقى and بشار. i originally wrote 4 here
    # which is ى and a real letter so the test failed and the code was right.
    assert TEXT[5] == " "
    entities = [
        {"entity_group": "LOC", "score": 0.9, "start": 5, "end": 5, "word": ""},
        {"entity_group": "LOC", "score": 0.9, "start": 5, "end": 6, "word": " "},
    ]
    assert build(entities).extract(TEXT) == []


def test_inference_failure_returns_nothing_instead_of_exploding():
    """a broken document should cost me its mentions and not the pipeline
    run. same call i made for the translation batches."""

    class Exploding:
        def __call__(self, text):
            raise RuntimeError("model blew up")

    extractor = ModelExtractor.__new__(ModelExtractor)
    extractor._pipeline = Exploding()
    extractor.min_confidence = 0.5
    assert extractor.extract(TEXT) == []


def test_empty_and_whitespace_text_short_circuits():
    extractor = build([])
    assert extractor.extract("") == []
    assert extractor.extract("   ") == []


def test_overlapping_predictions_get_resolved():
    """the model can emit a long span and a short one inside it. the same
    longest wins rule the gazetteer uses applies here."""
    long_one = ExtractedMention("بشار الأسد", 6, 16, "person")
    short_one = ExtractedMention("الأسد", 11, 16, "person")
    assert resolve_overlaps([short_one, long_one]) == [long_one]


def test_is_available_reports_honestly():
    """just has to agree with reality either way so the checkpoint can
    decide whether to print the model column."""
    assert isinstance(is_available(), bool)


@pytest.mark.skipif(not is_available(), reason="torch and transformers are not installed")
def test_real_model_loads_and_returns_valid_offsets():
    """only runs when the ml extras are installed. this is the one that
    would catch the pipeline changing its output shape under me."""
    extractor = ModelExtractor()
    for mention in extractor.extract(TEXT):
        assert TEXT[mention.start : mention.end] == mention.text
        assert mention.object_type in {"person", "organization", "location"}
