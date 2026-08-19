"""Tests for the translation cache (M5).

Uses EchoTranslator throughout — no API key, no network, no DeepL account
needed to run the suite. That's the whole reason `Translator` is a Protocol
with a test double rather than a module-level requests call.
"""

from __future__ import annotations

from sqlalchemy import select

from src.processing.translation import (
    DeepLTranslator,
    EchoTranslator,
    batched,
    source_sha256,
)
from src.store.orm import TranslationORM
from src.store.provenance import register_extractor_version
from src.store.translations import get_cached, translate_texts


class CountingTranslator:
    """Echo translator that records how many strings it was asked to
    translate, so tests can assert the cache actually prevented API calls."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def translate(self, texts: list[str]) -> list[str]:
        self.calls.append(list(texts))
        return [f"[en] {text}" for text in texts]

    @property
    def total_translated(self) -> int:
        return sum(len(batch) for batch in self.calls)


def _extractor(session):
    return register_extractor_version(session, "deepl_translator", "1.0.0")


def test_translate_and_read_back(session):
    extractor = _extractor(session)
    result = translate_texts(session, ["مرحبا بالعالم"], EchoTranslator(), extractor)

    assert result["مرحبا بالعالم"] == "[en] مرحبا بالعالم"
    assert get_cached(session, ["مرحبا بالعالم"])["مرحبا بالعالم"] == "[en] مرحبا بالعالم"


def test_second_run_hits_cache_and_calls_no_api(session):
    """The whole point of the cache: re-running the pipeline must not
    re-spend quota on text already translated."""
    extractor = _extractor(session)
    texts = ["عنوان الخبر الأول", "عنوان الخبر الثاني"]

    first = CountingTranslator()
    translate_texts(session, texts, first, extractor)
    assert first.total_translated == 2

    second = CountingTranslator()
    translate_texts(session, texts, second, extractor)
    assert second.total_translated == 0, "cached strings should never reach the translator"


def test_identical_text_from_two_documents_translated_once(session):
    """Content-addressing, not per-document keying: syndicated wire copy
    shares one translation."""
    extractor = _extractor(session)
    translator = CountingTranslator()
    duplicate = "نفس العنوان المنقول عن الوكالة"

    translate_texts(session, [duplicate, duplicate, duplicate], translator, extractor)

    assert translator.total_translated == 1
    rows = list(session.scalars(select(TranslationORM).where(TranslationORM.source_text == duplicate)))
    assert len(rows) == 1


def test_max_new_caps_api_usage(session):
    extractor = _extractor(session)
    translator = CountingTranslator()
    texts = [f"عنوان رقم {i}" for i in range(10)]

    result = translate_texts(session, texts, translator, extractor, max_new=3)

    assert translator.total_translated == 3
    assert len(result) == 3, "uncapped strings are simply left for the next run"


def test_failed_batch_does_not_abort_the_run(session):
    """A DeepL outage should cost translations, not the pipeline."""

    class BrokenTranslator:
        def translate(self, texts: list[str]) -> list[str]:
            raise RuntimeError("DeepL is down")

    extractor = _extractor(session)
    result = translate_texts(session, ["عنوان"], BrokenTranslator(), extractor)

    assert result == {}


def test_empty_and_whitespace_texts_are_skipped(session):
    extractor = _extractor(session)
    translator = CountingTranslator()

    result = translate_texts(session, ["", "   ", None], translator, extractor)  # type: ignore[list-item]

    assert result == {}
    assert translator.total_translated == 0


def test_translation_records_its_extractor_version(session):
    """P4: every derived output records which version produced it."""
    extractor = _extractor(session)
    translate_texts(session, ["نص"], EchoTranslator(), extractor)

    row = session.scalar(select(TranslationORM).where(TranslationORM.source_text == "نص"))
    assert row.extractor_version_id == extractor.id
    assert row.source_sha256 == source_sha256("نص")


def test_batched_splits_evenly_and_handles_remainder():
    assert batched([1, 2, 3, 4, 5], size=2) == [[1, 2], [3, 4], [5]]
    assert batched([], size=2) == []


def test_deepl_free_key_routes_to_the_free_host():
    """Free keys end in ':fx' and must hit api-free.deepl.com; sending one
    to the paid host returns an unhelpful 403."""
    assert "api-free.deepl.com" in DeepLTranslator("abc-123:fx")._url
    assert "api-free" not in DeepLTranslator("abc-123")._url
