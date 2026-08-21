"""transformer NER extractor.

wraps a huggingface token classification model. the gazetteer can only find
names i already wrote down. this one is supposed to find names it has never
seen which is the entire reason to pay for it.

two things make this harder than calling a pipeline.

first. the model does not see words it sees subword pieces. بارزاني can
come back as three tokens and each one gets its own tag. so B-PER followed
by I-PER followed by I-PER is one entity and not three and i have to merge
them. the brief calls this out and it is the part people get wrong.

second. getting character offsets back. rebuilding text from tokens loses
the original spacing and every offset drifts by a little which is worse
than being obviously broken. the fast tokenizers return offsets_mapping
which is the real character span of each token so i take the first token
start and the last token end and never reconstruct anything.

third thing worth saying. i feed this the ORIGINAL text and not the
normalized text. the model was trained on real arabic with diacritics and
punctuation so stripping all that first would hurt it. that is the opposite
of the gazetteer which needs normalized text to match its fixed strings.
the two extractors want different inputs and that is fine because the
interface only promises spans into the original.

torch and transformers are imported lazily inside the constructor. this
module gets imported by the checkpoint script and by tests that never build
a model and i do not want a 2GB import cost on either.
"""

from __future__ import annotations

import logging
from typing import Any

from src.extract.base import ExtractedMention, resolve_overlaps

logger = logging.getLogger("extract.model")

# CAMeL Lab trained this one on arabic NER. the brief names their camelbert
# family as the starting point.
DEFAULT_MODEL = "CAMeL-Lab/bert-base-arabic-camelbert-mix-ner"

# the model speaks in its own label set and my ontology has its own names.
# this is the only place the two meet.
_LABEL_TO_OBJECT_TYPE = {
    "PER": "person",
    "PERS": "person",
    "ORG": "organization",
    "LOC": "location",
    # GPE is geo political entity. countries and cities land here in some
    # tag sets and my ontology calls all of that a location
    "GPE": "location",
}


class ModelExtractor:
    name = "camelbert_ner_extractor"
    language = "ar"
    version = "1.0.0"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        min_confidence: float = 0.5,
        aggregation_strategy: str = "average",
    ) -> None:
        # imported here and not at module top so the checkpoint script can
        # import this file to check whether the model is available without
        # paying for torch when it is not
        from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

        self.model_name = model_name
        self.min_confidence = min_confidence

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForTokenClassification.from_pretrained(model_name)

        # this argument matters more than it looks and i got it wrong first
        # time. i started on simple and the eval set caught it immediately.
        #
        # simple merges consecutive tokens that share a tag but it does NOT
        # group subword pieces into words first. so when the model tagged
        # مسرور as two pieces and started a fresh B- on the second one i got
        # مس and رور بارزاني as two separate people instead of one. exactly
        # the subword failure this whole class is supposed to handle.
        #
        # average groups the pieces of a word together first and then
        # averages the scores across them. first and max also group properly
        # and differ only in which score they keep. i went with average
        # because the confidence then reflects the whole word rather than
        # whichever piece happened to come first.
        self._pipeline = pipeline(
            "token-classification",
            model=model,
            tokenizer=tokenizer,
            aggregation_strategy=aggregation_strategy,
        )

    def extract(self, text: str) -> list[ExtractedMention]:
        if not text or not text.strip():
            return []

        try:
            raw_entities: list[dict[str, Any]] = self._pipeline(text)
        except Exception:
            # a failure here should cost me mentions and not the whole run.
            # same reasoning as the translation batches.
            logger.exception("model inference failed on a document. returning nothing")
            return []

        mentions: list[ExtractedMention] = []
        for entity in raw_entities:
            object_type = _LABEL_TO_OBJECT_TYPE.get(entity.get("entity_group", "").upper())
            if object_type is None:
                continue

            score = float(entity.get("score", 0.0))
            if score < self.min_confidence:
                continue

            start = int(entity["start"])
            end = int(entity["end"])
            if start >= end:
                continue

            # i slice the original text myself rather than trusting the word
            # field the pipeline returns. that field comes from decoding
            # tokens back to a string and it can differ from the source by a
            # space or a stripped character. the offsets are the truth so
            # the text has to come from them or P2 quietly breaks.
            surface = text[start:end]
            if not surface.strip():
                continue

            mentions.append(
                ExtractedMention(
                    text=surface,
                    start=start,
                    end=end,
                    object_type=object_type,
                    confidence=score,
                )
            )

        return resolve_overlaps(mentions)


def is_available() -> bool:
    """whether torch and transformers are installed.

    lets the checkpoint print the gazetteer column on its own instead of
    crashing when the ml extras are not installed.
    """
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True
