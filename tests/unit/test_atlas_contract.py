"""Offline archive boundary checks; runnable with unittest and Pydantic only."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from src.contracts.atlas import (
    AtlasSnapshot,
    atlas_snapshot_id,
    build_atlas_snapshot,
    validate_atlas_bundle,
    validate_atlas_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]


def fixture_bundle():
    dashboard = json.loads((ROOT / "contracts/dashboard/1.0.0/dashboard.fixture.json").read_text())
    country = json.loads((ROOT / "contracts/dashboard/1.0.0/country.fixture.json").read_text())
    archive = json.loads((ROOT / "contracts/atlas/1.0.0/atlas.fixture.json").read_text())
    return dashboard, {country["country"]: country}, archive


def rehash(payload):
    payload["snapshot_id"] = atlas_snapshot_id(payload)
    return payload


class AtlasContractTests(unittest.TestCase):
    def test_fixture_is_valid_with_existing_v1(self):
        data, pages, archive = fixture_bundle()
        self.assertEqual(validate_atlas_bundle(data, pages, archive), (data, pages, archive))
        self.assertEqual(archive["scope"], "current_processed_live_documents")
        self.assertEqual(archive["entity_id_scope"], "snapshot_local")
        self.assertEqual(archive["translation_scope"], "cached_machine_translation")

    def test_snapshot_hash_detects_changed_content_even_at_same_time(self):
        _, _, archive = fixture_bundle()
        archive["articles"][0]["title"] = "Different title"
        with self.assertRaisesRegex(ValueError, "snapshot_id"):
            validate_atlas_snapshot(archive)

    def test_exact_id_membership_rejects_same_count_replacement(self):
        _, _, archive = fixture_bundle()
        archive["articles"][0]["id"] = "replacement"
        with self.assertRaisesRegex(ValueError, "exactly the current processed document IDs"):
            validate_atlas_snapshot(rehash(archive), expected_document_ids={"1"})

    def test_duplicate_identities_and_memberships_are_rejected(self):
        for field in ("articles", "entities"):
            with self.subTest(field=field):
                _, _, archive = fixture_bundle()
                archive[field].append(copy.deepcopy(archive[field][0]))
                with self.assertRaisesRegex(ValueError, "identities must be unique"):
                    validate_atlas_snapshot(rehash(archive))
        _, _, archive = fixture_bundle()
        archive["articles"][0]["entity_ids"].append(archive["entities"][0]["id"])
        with self.assertRaisesRegex(ValueError, "memberships must be unique"):
            validate_atlas_snapshot(rehash(archive))

    def test_dangling_and_unused_entities_are_rejected(self):
        _, _, archive = fixture_bundle()
        archive["articles"][0]["entity_ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "absent entity"):
            validate_atlas_snapshot(rehash(archive))
        archive["articles"][0]["entity_ids"] = []
        with self.assertRaisesRegex(ValueError, "no current article membership"):
            validate_atlas_snapshot(rehash(archive))

    def test_unsafe_urls_are_rejected(self):
        for url in (
            "javascript:alert(1)", "/relative", "https://user:secret@example.com/a",
            "https://example.com/a path", "https://example.com/\\@bad.example",
            'https://example.com/" onload="attack',
        ):
            with self.subTest(url=url):
                _, _, archive = fixture_bundle()
                archive["articles"][0]["url"] = url
                with self.assertRaises(ValueError):
                    validate_atlas_snapshot(rehash(archive))

    def test_private_fields_and_arbitrary_classifier_fields_are_rejected(self):
        for extra in ("body", "text", "excerpt", "text_blob_key", "start_offset"):
            with self.subTest(extra=extra):
                _, _, archive = fixture_bundle()
                archive["articles"][0][extra] = "private source content"
                with self.assertRaises(ValueError):
                    validate_atlas_snapshot(rehash(archive))
        _, _, archive = fixture_bundle()
        archive["classification"]["extra"] = "not allowed"
        with self.assertRaises(ValueError):
            validate_atlas_snapshot(rehash(archive))

    def test_scope_and_completeness_cannot_be_silently_redefined(self):
        for field, value in (("scope", "all_history"), ("complete", False), ("entity_id_scope", "durable")):
            with self.subTest(field=field):
                _, _, archive = fixture_bundle()
                archive[field] = value
                with self.assertRaises(ValueError):
                    validate_atlas_snapshot(rehash(archive))

    def test_literal_json_types_are_strict(self):
        for field, value in (("complete", 1), ("atlas_version", True)):
            with self.subTest(field=field):
                _, _, archive = fixture_bundle()
                archive[field] = value
                with self.assertRaisesRegex(ValueError, "wrong JSON type"):
                    validate_atlas_snapshot(rehash(archive))

    def test_empty_snapshot_and_undated_unassigned_articles_are_valid(self):
        _, _, fixture = fixture_bundle()
        article = copy.deepcopy(fixture["articles"][0])
        article.update(country=None, published_date=None, collected_at=None, entity_ids=[])
        for rows in ([], [article]):
            archive = build_atlas_snapshot(
                generated_at=fixture["generated_at"], classification=fixture["classification"],
                articles=rows, entities=[], expected_document_ids={row["id"] for row in rows},
            )
            self.assertEqual(len(archive["articles"]), len(rows))
            if rows:
                self.assertIsNone(archive["articles"][0]["published_date"])
                self.assertIsNone(archive["articles"][0]["country"])

    def test_bundle_rejects_timestamp_drift(self):
        data, pages, archive = fixture_bundle()
        archive["generated_at"] = "2026-08-21T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "same snapshot"):
            validate_atlas_bundle(data, pages, rehash(archive))

    def test_equal_counts_do_not_hide_wrong_country_sample_membership(self):
        data, pages, archive = fixture_bundle()
        pages["Syria"]["articles"][0]["title"] = "Another document with the same counts"
        with self.assertRaisesRegex(ValueError, "metadata absent"):
            validate_atlas_bundle(data, pages, archive)

    def test_equal_counts_do_not_hide_wrong_recent_membership(self):
        data, pages, archive = fixture_bundle()
        data["recent"][0]["url"] = "https://example.com/different-article"
        with self.assertRaisesRegex(ValueError, "recent feed.*metadata absent"):
            validate_atlas_bundle(data, pages, archive)

    def test_country_membership_cannot_be_relabelled_with_equal_article_count(self):
        data, pages, archive = fixture_bundle()
        archive["articles"][0]["country"] = None
        with self.assertRaisesRegex(ValueError, "country membership"):
            validate_atlas_bundle(data, pages, rehash(archive))

    def test_duplicate_country_sample_is_not_another_document(self):
        data, pages, archive = fixture_bundle()
        second = copy.deepcopy(archive["articles"][0])
        second.update(id="2", url="https://example.com/another", country="Syria")
        archive["articles"].append(second)
        data["stats"].update(total_raw=3, total_processed=2)
        data["stats"]["sources"]["AlJazeeraArabic"] = 2
        data["topics"]["topics"][0]["count"] = 2
        data["escalation"]["escalation"]["high"] = 2
        data["countries"][0]["count"] = 2
        page = pages["Syria"]
        page.update(total=2, sources={"AlJazeeraArabic": 2}, escalation={"high": 2})
        page["topics"][0]["count"] = 2
        page["articles"].append(copy.deepcopy(page["articles"][0]))
        with self.assertRaisesRegex(ValueError, "metadata absent"):
            validate_atlas_bundle(data, pages, rehash(archive))

    def test_original_text_and_safe_urls_are_preserved_exactly(self):
        _, _, archive = fixture_bundle()
        article = archive["articles"][0]
        article["url"] = "HTTPS://EXAMPLE.COM/source?id=1&lang=ar"
        article["title"] = "خَبَر <script> & original"
        self.assertEqual(validate_atlas_snapshot(rehash(archive))["articles"][0], article)

    def test_builder_order_is_deterministic_and_does_not_mutate_inputs(self):
        _, _, fixture = fixture_bundle()
        article = fixture["articles"][0]
        second = {**article, "id": "2"}
        before = copy.deepcopy(fixture)
        first = build_atlas_snapshot(
            generated_at=fixture["generated_at"], classification=fixture["classification"],
            articles=[second, article], entities=fixture["entities"],
        )
        reordered = build_atlas_snapshot(
            generated_at=fixture["generated_at"], classification=fixture["classification"],
            articles=[article, second], entities=fixture["entities"],
        )
        self.assertEqual(first, reordered)
        self.assertEqual(fixture, before)

    def test_schema_matches_pinned_contract_model(self):
        schema = json.loads((ROOT / "contracts/atlas/1.0.0/atlas.schema.json").read_text())
        self.assertEqual(schema, AtlasSnapshot.model_json_schema())


if __name__ == "__main__":
    unittest.main()
