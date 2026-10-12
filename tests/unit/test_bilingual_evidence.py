"""Fixture tests for private search, provenance, bounds, and fail-closed inputs."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.core.models import Document, Entity, ExtractorVersion, Mention
from src.lang import ArabicAdapter
from src.search.evidence import (
    CachedEvidenceTranslation,
    EntityMention,
    EvidenceCorpus,
    EvidenceInputError,
    SearchEntity,
    SourceDocument,
    VerifiedAlias,
    search_evidence,
    text_sha256,
    validate_corpus,
)
from src.search.evidence_cli import load_snapshot, main

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "bilingual_evidence.json"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def corpus():
    return load_snapshot(SAMPLE)


def _single(text: str, surface: str = "محمد", *, start: int | None = None) -> EvidenceCorpus:
    if start is None:
        start = text.index(surface)
    document = Document(1, "fixture", text, "ingest-hash-is-not-text-sha256")
    return EvidenceCorpus(
        documents=(SourceDocument(document, "ar", text_sha256(text), len(text)),),
        entities=(SearchEntity(Entity(1, "person", "محمد", {}), "ar"),),
        mentions=(Mention(1, 1, surface, start, start + len(surface), "person", 1),),
        memberships=(EntityMention(1, 1),),
        extractor_versions=(ExtractorVersion(1, "fixture", "1.0.0"),),
        data_kind="synthetic_fixture",
    )


def _cache(corpus, *, origin="synthetic_fixture", output="Synthetic translation for a unit test."):
    source = corpus.documents[0]
    sentence = ArabicAdapter().segment_sentences(source.document.text)[0]
    return CachedEvidenceTranslation(
        document_id=source.document.id, document_text_sha256=source.text_sha256,
        start=sentence.start, end=sentence.end, source_text_sha256=text_sha256(sentence.text),
        source_lang="ar", target_lang="en", artifact_ref="fixture:cached-artifact-1",
        provider="fixture-provider", provider_version="1.0.0", settings_sha256=text_sha256("{}"),
        output_text=output, record_origin=origin,
    )


def _first(corpus, query="mohammed", **kwargs):
    return search_evidence(corpus, query, **kwargs)["results"][0]


def test_sample_arabic_romanization_and_attested_alias_queries(corpus):
    for query, expected_kind in [
        ("مُحَمَّد الاحمد", "original"),
        ("muhammad ahmad", "romanization"),
        ("MOHAMMED AL-AHMAD", "verified_alias"),
    ]:
        result = search_evidence(corpus, query)
        assert result["data_kind"] == "synthetic_fixture"
        assert result["entity_id_scope"] == "snapshot_local"
        assert result["access_scope"] == "private_local_only"
        assert result["matched_entity_count"] == 1
        row = result["results"][0]
        assert row["entity_id"] == 1
        assert expected_kind in {match["name"]["kind"] for match in row["matched_names"]}
        assert len(row["evidence"]) == 2
        assert all(item["translation_status"] == "unavailable_for_exact_window" for item in row["evidence"])
        assert all(item["translation"] is None for item in row["evidence"])


def test_sample_has_real_paragraph_newlines(corpus):
    text = corpus.documents[1].document.text
    assert [index for index, char in enumerate(text) if ord(char) == 10] == [50, 51]
    assert "\\n" not in text
    assert len(ArabicAdapter().segment_paragraphs(text)) == 2


def test_typed_names_keep_romanization_separate_from_alias_and_translation(corpus):
    names = _first(corpus)["names"]
    assert {name["kind"] for name in names} == {"original", "romanization", "verified_alias"}
    romanized = [name for name in names if name["kind"] == "romanization"]
    assert romanized and all(name["language"] == "ar-Latn" for name in romanized)
    assert all(name["producer"] == "ArabicAdapter.romanize" for name in romanized)
    alias = [name for name in names if name["kind"] == "verified_alias"][0]
    assert alias["verifier"] == "synthetic-fixture-author"
    assert alias["verification_ref"].startswith("fixture:")
    assert alias["evidence_mention_id"] == 1
    assert not any(name["kind"] == "translation" for name in names)


def test_every_result_proves_original_text_offsets_hash_and_extractor(corpus):
    result = _first(corpus)
    documents = {row.document.id: row for row in corpus.documents}
    for item in result["evidence"]:
        source = documents[item["document_id"]]
        text = source.document.text
        assert text[item["start"]:item["end"]] == item["text"]
        assert item["span_sha256"] == text_sha256(item["text"])
        assert item["document_text_sha256"] == text_sha256(text)
        assert item["document_text_length"] == len(text)
        assert item["content_hash"] != item["document_text_sha256"]
        mention = item["mention"]
        assert text[mention["start"]:mention["end"]] == mention["text"]
        assert item["start"] <= mention["start"] < mention["end"] <= item["end"]
        assert mention["extractor_name"] == "synthetic_fixture_extractor"
        assert mention["extractor_version"] == "1.0.0"
        assert item["segmenter"] == {"name": "arabic_rule_segmenter", "version": "1.0.0"}
    assert "مُحَمَّد" in result["evidence"][0]["text"]
    assert "🗞️" in result["evidence"][1]["text"]


@pytest.mark.parametrize("query", ["", "  ", "؟!", "غير موجود", "nobody-here"])
def test_empty_or_unmatched_query_returns_nothing(corpus, query):
    assert search_evidence(corpus, query)["results"] == []


def test_unicode_presentation_forms_keep_original_source():
    corpus = _single("تم لقاء مُـحَمَّد ﷲ في المكتبة.", "مُـحَمَّد")
    item = _first(corpus)["evidence"][0]
    assert item["text"] == corpus.documents[0].document.text
    assert item["mention"]["text"] == "مُـحَمَّد"


@pytest.mark.parametrize("position", [0, 20, 100])
def test_cropped_sentence_window_is_exact_and_contains_whole_mention(position):
    text = "س" * position + "محمد" + "ب" * (120 - position) + "."
    corpus = _single(text)
    item = _first(corpus, span_characters=24)["evidence"][0]
    assert item["span_kind"] == "sentence_window"
    assert item["truncated"] is True
    assert item["sentence_start"] == 0
    assert item["sentence_end"] == len(text)
    assert len(item["text"]) == 24
    assert "محمد" in item["text"]
    assert text[item["start"]:item["end"]] == item["text"]
    assert "…" not in item["text"]  # display ellipses are never injected into the evidence


def test_cross_sentence_mention_is_honestly_a_mention_span():
    corpus = _single("قال محمد. ثم عاد أحمد.", "محمد. ثم عاد أحمد")
    item = _first(corpus)["evidence"][0]
    assert item["span_kind"] == "mention_span"
    assert item["sentence_start"] is None
    assert item["sentence_end"] is None
    assert item["truncated"] is False
    assert item["text"] == "محمد. ثم عاد أحمد"


def test_oversized_mention_is_omitted_rather_than_cut_or_allowed_to_bypass_cap():
    corpus = _single("قال محمد الأحمد اليوم.", "محمد الأحمد")
    row = _first(corpus, span_characters=4)
    assert row["evidence"] == []
    assert row["evidence_coverage"]["mentions_exceeding_span_limit"] == 1
    assert row["evidence_coverage"]["status"] == "no_bounded_live_evidence"


def test_repeated_mentions_in_same_sentence_do_not_repeat_evidence():
    corpus = _single("قال محمد إن محمد سيتحدث غداً.")
    start = corpus.documents[0].document.text.rindex("محمد")
    other = replace(corpus.mentions[0], id=2, start=start, end=start + 4)
    corpus = replace(corpus, mentions=(*corpus.mentions, other), memberships=(EntityMention(1, 1), EntityMention(1, 2)))
    row = _first(corpus)
    assert len(row["evidence"]) == 1
    assert row["evidence_coverage"]["active_linked_mentions"] == 2
    assert row["evidence_coverage"]["unique_windows"] == 1


def test_result_and_evidence_limits_report_remaining_work(corpus):
    # Arabic substring appears in both entities, but exact matches rank first.
    result = search_evidence(corpus, "ح", limit=1, evidence_per_entity=1)
    assert result["matched_entity_count"] == 2
    assert result["has_more_entities"] is True
    row = result["results"][0]
    assert len(row["evidence"]) == 1
    assert row["evidence_coverage"]["has_more_windows"] is True


def test_results_do_not_depend_on_input_row_order(corpus):
    reverse = replace(
        corpus, documents=tuple(reversed(corpus.documents)), entities=tuple(reversed(corpus.entities)),
        mentions=tuple(reversed(corpus.mentions)), memberships=tuple(reversed(corpus.memberships)),
    )
    assert search_evidence(corpus, "ح") == search_evidence(reverse, "ح")


def test_exact_match_ranks_before_substring_independent_of_entity_id():
    corpus = _single("قال محمد الخبر.")
    corpus = replace(corpus, entities=(
        SearchEntity(Entity(1, "person", "محمد الأحمد", {}), "ar"),
        SearchEntity(Entity(2, "person", "محمد", {}), "ar"),
    ))
    assert [row["entity_id"] for row in search_evidence(corpus, "محمد")["results"]] == [2, 1]


def test_retracted_entity_is_excluded(corpus):
    row = corpus.entities[0]
    corpus = replace(corpus, entities=(replace(row, entity=replace(row.entity, retracted=True)), corpus.entities[1]))
    assert search_evidence(corpus, "mohammed")["results"] == []


@pytest.mark.parametrize("retract", ["document", "mention"])
def test_retracted_evidence_and_its_alias_are_excluded(corpus, retract):
    if retract == "document":
        source = corpus.documents[0]
        corpus = replace(corpus, documents=(replace(source, document=replace(source.document, retracted=True)), corpus.documents[1]))
    else:
        corpus = replace(corpus, mentions=(replace(corpus.mentions[0], retracted=True), *corpus.mentions[1:]))
    assert search_evidence(corpus, "Mohammed al-Ahmad")["results"] == []
    row = _first(corpus)
    assert len(row["evidence"]) == 1
    assert row["evidence"][0]["document_id"] == 2
    assert not any(name["kind"] == "verified_alias" for name in row["names"])


def test_entity_with_no_live_evidence_does_not_fabricate_a_span():
    corpus = _single("قال محمد الخبر.")
    corpus = replace(corpus, mentions=(replace(corpus.mentions[0], retracted=True),))
    row = _first(corpus)
    assert row["evidence"] == []
    assert row["evidence_coverage"]["status"] == "no_bounded_live_evidence"


def test_english_adapter_is_usable_without_fake_romanization():
    text = "Dr. Smith visited the library."
    corpus = _single(text, "Smith")
    corpus = replace(corpus,
        documents=(replace(corpus.documents[0], language="en"),),
        entities=(SearchEntity(Entity(1, "person", "Smith", {}), "en"),),
    )
    row = _first(corpus, "SMITH")
    assert len(row["names"]) == 1
    assert row["names"][0]["kind"] == "original"
    assert row["evidence"][0]["text"] == text
    assert row["evidence"][0]["segmenter"]["name"] == "english_rule_segmenter"


def test_fixture_translation_never_claims_real_machine_output(corpus):
    corpus = replace(corpus, translations=(_cache(corpus),))
    item = _first(corpus)["evidence"][0]
    assert item["translation_status"] == "fixture_only"
    assert item["translation"]["kind"] == "synthetic_fixture_translation"
    assert item["translation"]["machine_generated"] is False
    assert item["translation"]["source_text_sha256"] == item["span_sha256"]


@pytest.mark.parametrize("output,status", [("Cached test output", "cached"), ("", "cached_empty")])
def test_detached_persisted_cache_record_preserves_provenance_and_empty_result(corpus, output, status):
    # Simulated cache input only; no real provider is called by this test.
    corpus = replace(corpus, data_kind="offline_snapshot", translations=(_cache(corpus, origin="persisted_cache", output=output),))
    item = _first(corpus)["evidence"][0]
    assert item["translation_status"] == status
    assert item["translation"]["kind"] == "cached_machine_translation"
    assert item["translation"]["machine_generated"] is True
    assert item["translation"]["artifact_ref"] == "fixture:cached-artifact-1"
    assert item["translation"]["provider_version"] == "1.0.0"
    assert item["translation"]["output_text"] == output


def test_whole_sentence_translation_never_attaches_to_cropped_window(corpus):
    corpus = replace(corpus, translations=(_cache(corpus),))
    item = _first(corpus, span_characters=25)["evidence"][0]
    assert item["span_kind"] == "sentence_window"
    assert item["translation_status"] == "unavailable_for_exact_window"
    assert item["translation"] is None


def test_cached_translation_is_language_scoped(corpus):
    corpus = replace(corpus, translations=(_cache(corpus),))
    assert _first(corpus, target_language="ar")["evidence"][0]["translation"] is None


def test_oversized_cached_output_is_not_returned(corpus):
    corpus = replace(corpus, translations=(_cache(corpus, output="t" * 1201),))
    item = _first(corpus)["evidence"][0]
    assert item["translation"] is None
    assert item["translation_status"] == "translation_output_exceeds_limit"


@pytest.mark.parametrize("change", [
    {"source_text_sha256": "0" * 64}, {"document_text_sha256": "0" * 64},
    {"start": -1}, {"end": 9999}, {"source_lang": "en"},
    {"artifact_ref": ""}, {"provider": ""}, {"provider_version": ""},
    {"settings_sha256": "made-up"}, {"record_origin": ""},
    {"record_origin": "persisted_cache"}, {"output_text": None},
])
def test_stale_or_incomplete_translation_is_an_explicit_error(corpus, change):
    corpus = replace(corpus, translations=(replace(_cache(corpus), **change),))
    with pytest.raises(EvidenceInputError):
        search_evidence(corpus, "no match")


def test_ambiguous_cache_selection_is_rejected(corpus):
    original = _cache(corpus)
    corpus = replace(corpus, translations=(original, replace(original, provider="another-provider")))
    with pytest.raises(EvidenceInputError, match="ambiguous"):
        validate_corpus(corpus)


def test_conflicting_output_for_one_content_address_is_rejected():
    corpus = _single("قال محمد الخبر. قال محمد الخبر.")
    first = _cache(corpus)
    second_start = corpus.documents[0].document.text.rindex("قال")
    second = replace(first, start=second_start, end=len(corpus.documents[0].document.text), output_text="Different output")
    corpus = replace(corpus, translations=(first, second))
    with pytest.raises(EvidenceInputError, match="conflicting outputs"):
        validate_corpus(corpus)


def test_artifact_reference_cannot_change_its_provenance(corpus):
    first = _cache(corpus)
    source = corpus.documents[1]
    sentence = ArabicAdapter().segment_sentences(source.document.text)[0]
    second = replace(first, document_id=source.document.id, document_text_sha256=source.text_sha256,
                     start=sentence.start, end=sentence.end, source_text_sha256=text_sha256(sentence.text))
    with pytest.raises(EvidenceInputError, match="conflicting provenance"):
        validate_corpus(replace(corpus, translations=(first, second)))


@pytest.mark.parametrize("change", [{"text_sha256": "0" * 64}, {"text_length": 2}, {"text_length": True}])
def test_document_hash_and_length_are_required_and_verified(corpus, change):
    corpus = replace(corpus, documents=(replace(corpus.documents[0], **change), corpus.documents[1]))
    with pytest.raises(EvidenceInputError):
        search_evidence(corpus, "no match")


@pytest.mark.parametrize("change", [
    {"start": -1}, {"end": 9999}, {"start": 25, "end": 24}, {"start": True},
    {"text": "مختلف"}, {"document_id": 999}, {"extractor_version_id": 999},
    {"id": True}, {"id": 0}, {"retracted": "false"},
])
def test_invalid_mention_never_produces_partial_results(corpus, change):
    corpus = replace(corpus, mentions=(replace(corpus.mentions[0], **change), *corpus.mentions[1:]))
    with pytest.raises(EvidenceInputError):
        search_evidence(corpus, "mohammed")


@pytest.mark.parametrize("change", [
    {"verifier": ""}, {"verification_ref": ""}, {"entity_id": 2},
    {"evidence_mention_id": 999}, {"text": ""}, {"entity_id": True},
])
def test_alias_needs_valid_entity_bound_attestation(corpus, change):
    corpus = replace(corpus, aliases=(replace(corpus.aliases[0], **change),))
    with pytest.raises(EvidenceInputError):
        validate_corpus(corpus)


@pytest.mark.parametrize("collection", ["documents", "entities", "mentions", "extractor_versions", "memberships", "aliases"])
def test_duplicate_identifiers_or_links_are_rejected(corpus, collection):
    rows = getattr(corpus, collection)
    corpus = replace(corpus, **{collection: (*rows, rows[0])})
    with pytest.raises(EvidenceInputError, match="duplicate"):
        validate_corpus(corpus)


@pytest.mark.parametrize("link", [EntityMention(999, 1), EntityMention(1, 999), EntityMention(True, 1)])
def test_missing_or_invalid_membership_references_rejected(corpus, link):
    with pytest.raises(EvidenceInputError):
        validate_corpus(replace(corpus, memberships=(link,)))


def test_membership_cannot_cross_object_types(corpus):
    entity = replace(corpus.entities[0].entity, object_type="organization")
    corpus = replace(corpus, entities=(replace(corpus.entities[0], entity=entity), corpus.entities[1]))
    with pytest.raises(EvidenceInputError, match="object types"):
        validate_corpus(corpus)


def test_live_mention_cannot_belong_to_two_live_entities(corpus):
    with pytest.raises(EvidenceInputError, match="multiple live entities"):
        validate_corpus(replace(corpus, memberships=(*corpus.memberships, EntityMention(2, 1))))


def test_historical_retracted_entity_link_does_not_claim_live_ownership(corpus):
    historical = SearchEntity(Entity(3, "person", "محمد", {}, retracted=True), "ar")
    corpus = replace(corpus, entities=(*corpus.entities, historical), memberships=(*corpus.memberships, EntityMention(3, 1)))
    validate_corpus(corpus)
    result = search_evidence(corpus, "محمد")
    assert [row["entity_id"] for row in result["results"]] == [1]


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///tmp/data", "//example.com", "https://user:pass@example.com",
    "https://example.com/\"bad", "https://example.com/\\bad", "https://example.com/<script>",
    "https://example.com/ has-space", "https://example.com/\x00", "https://example.com:99999/",
])
def test_unsafe_source_urls_are_rejected(corpus, url):
    source = corpus.documents[0]
    corpus = replace(corpus, documents=(replace(source, document=replace(source.document, url=url)), corpus.documents[1]))
    with pytest.raises(EvidenceInputError, match="source URL"):
        validate_corpus(corpus)


def test_external_text_is_preserved_as_text_not_html_escaped():
    corpus = _single("قال محمد <script>alert(1)</script>.")
    assert "<script>" in _first(corpus)["evidence"][0]["text"]


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"evidence_per_entity": 0},
    {"evidence_per_entity": 6}, {"span_characters": 0}, {"span_characters": 601},
    {"query": "q" * 257}, {"query": None},
    {"query": "\ud800"},
])
def test_limits_are_hard_validated(corpus, kwargs):
    values = {"query": "محمد", **kwargs}
    with pytest.raises(EvidenceInputError):
        search_evidence(corpus, **values)


def test_cli_runs_with_only_standard_library_and_repository(tmp_path):
    result = subprocess.run([
        sys.executable, "-S", "-m", "src.search.evidence_cli", "--snapshot", str(SAMPLE),
        "--query", "Mohammed al-Ahmad", "--limit", "1",
    ], cwd=ROOT, capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output["results"][0]["entity_id"] == 1
    assert output["results"][0]["evidence"][0]["text"].startswith("في تقرير تجريبي")
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_corrupt_snapshot_without_partial_stdout(tmp_path, capsys):
    data = json.loads(SAMPLE.read_text())
    data["documents"][0]["text_sha256"] = "0" * 64
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(data))
    assert main(["--snapshot", str(path), "--query", "محمد"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "source text hash or length mismatch" in captured.err


@pytest.mark.parametrize("payload", [
    "[]", '{"schema_version": "offline-evidence-v1", "schema_version": "bad"}',
    '{"schema_version": NaN}', '{"schema_version": "other"}',
])
def test_loader_rejects_ambiguous_or_malformed_json(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(payload)
    with pytest.raises(EvidenceInputError):
        load_snapshot(path)


def test_loader_never_silently_ignores_unknown_fields(tmp_path):
    data = json.loads(SAMPLE.read_text())
    data["stable_generation_id"] = 42
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(EvidenceInputError, match="unknown snapshot"):
        load_snapshot(path)


def test_loader_requires_explicit_data_kind(tmp_path):
    data = json.loads(SAMPLE.read_text())
    del data["data_kind"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(EvidenceInputError, match="data_kind"):
        load_snapshot(path)


def test_loader_bounds_bytes_before_json_decode(tmp_path, monkeypatch):
    import src.search.evidence_cli as cli
    monkeypatch.setattr(cli, "MAX_SNAPSHOT_BYTES", 10)
    path = tmp_path / "large.json"
    path.write_bytes(b" " * 11)
    with pytest.raises(EvidenceInputError, match="input limit"):
        load_snapshot(path)


@pytest.mark.parametrize("field,value", [("language", []), ("language", "unknown"), ("text", "\ud800")])
def test_malformed_string_values_fail_cleanly(tmp_path, capsys, field, value):
    data = json.loads(SAMPLE.read_text())
    data["documents"][0][field] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    assert main(["--snapshot", str(path), "--query", "محمد"]) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("collection,maximum", [("entities", 1000), ("documents", 500), ("mentions", 10000)])
def test_corpus_count_limits_are_checked_before_rows(corpus, collection, maximum):
    rows = (getattr(corpus, collection)[0],) * (maximum + 1)
    with pytest.raises(EvidenceInputError, match="too many"):
        validate_corpus(replace(corpus, **{collection: rows}))


def test_total_source_character_budget_is_bounded(corpus, monkeypatch):
    import src.search.evidence as evidence
    maximum_document = max(len(row.document.text) for row in corpus.documents)
    monkeypatch.setattr(evidence, "MAX_SOURCE_CHARACTERS", maximum_document)
    with pytest.raises(EvidenceInputError, match="total source character"):
        validate_corpus(corpus)
