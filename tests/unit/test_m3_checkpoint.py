"""M3 saved reports reuse the existing evaluator and preserve source evidence.

unittest keeps these focused tests runnable without the database/pytest
fixtures too: python -m unittest discover -s tests/unit -p test_m3_checkpoint.py
Fake model outputs below test reporting, never a model benchmark.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import m3_checkpoint as checkpoint
from src.extract.base import ExtractedMention
from src.extract.eval_set import EvalDocument, load_eval_set
from src.extract.evaluate import score_by_type, score_corpus
from src.resolve.learning import content_sha256


class FakeExtractor:
    name = "fake_test_extractor"
    version = "1.2.3"
    language = "ar"

    def __init__(self, predictions=None, **settings):
        self.predictions = predictions or {}

    def extract(self, text):
        return list(self.predictions.get(text, []))


class LoggingErrorExtractor(FakeExtractor):
    def extract(self, text):
        if text:
            try:
                raise RuntimeError("private path must not enter the report: /private/cache")
            except RuntimeError:
                logging.getLogger("extract.model").exception("inference failed")
        return []


class ReportEvidenceTests(unittest.TestCase):
    def test_exact_match_metrics_and_duplicate_errors_match_existing_evaluator(self):
        text = "مصر ثم تونس"
        egypt = ExtractedMention("مصر", 0, 3, "location")
        tunis = ExtractedMention("تونس", 7, 11, "location")
        wrong_type = ExtractedMention("مصر", 0, 3, "person", 0.7)
        document = EvalDocument(text, (egypt, tunis))
        extractor = FakeExtractor({text: [wrong_type, tunis, wrong_type]})
        evaluation = checkpoint.evaluate(extractor, [document])
        report = checkpoint.extractor_report(extractor, [document], evaluation, settings={})

        pairs = evaluation[0]
        self.assertEqual(report["overall"], checkpoint.score_record(score_corpus(pairs)))
        self.assertEqual(report["per_type"], {
            key: checkpoint.score_record(value) for key, value in score_by_type(pairs).items()
        })
        self.assertEqual(report["overall"]["true_positives"], 1)
        self.assertEqual(report["overall"]["false_positives"], 1)
        self.assertEqual(report["overall"]["false_negatives"], 1)
        evidence = report["evidence"][0]
        self.assertEqual(len(evidence["predicted"]), 3)  # raw duplicate is still inspectable
        self.assertEqual(len(evidence["false_positives"]), 1)
        self.assertEqual(len(evidence["false_negatives"]), 1)
        self.assertEqual(evidence["false_positives"][0]["object_type"], "person")
        self.assertEqual(evidence["false_negatives"][0]["object_type"], "location")
        self.assertEqual(report["name"], extractor.name)
        self.assertEqual(report["version"], extractor.version)

    def test_all_errors_exported_even_after_console_limit(self):
        documents = [EvalDocument("مصر", (ExtractedMention("مصر", 0, 3, "location"),))] * 9
        extractor = FakeExtractor()
        report = checkpoint.extractor_report(
            extractor, documents, checkpoint.evaluate(extractor, documents), settings={},
        )
        self.assertEqual(len(report["evidence"]), 9)
        self.assertEqual(sum(len(row["false_negatives"]) for row in report["evidence"]), 9)

    def test_input_order_does_not_change_evidence_serialization(self):
        text = "مصر ثم تونس"
        spans = [ExtractedMention("مصر", 0, 3, "place"), ExtractedMention("تونس", 7, 11, "place")]
        self.assertEqual(checkpoint.mention_rows(spans, text), checkpoint.mention_rows(spans[::-1], text))

    def test_unicode_offsets_are_code_points_not_bytes(self):
        text = "📰 زار مصر"
        span = ExtractedMention("مصر", 6, 9, "arbitrary_configured_type")
        row = checkpoint.mention_rows([span], text)[0]
        self.assertEqual(text[row["start"]:row["end"]], row["text"])
        self.assertEqual(row["object_type"], "arbitrary_configured_type")

    def test_invalid_source_offsets_fail_before_scoring(self):
        invalid = [
            ExtractedMention("مصر", -3, 3, "location"),
            ExtractedMention("مصر", 0, 99, "location"),
            ExtractedMention("مصر", 0, 2, "location"),
            ExtractedMention("", 0, 0, "location"),
            ExtractedMention("تونس", 0, 3, "location"),
        ]
        document = EvalDocument("مصر", ())
        for mention in invalid:
            with self.subTest(mention=mention), self.assertRaisesRegex(ValueError, "offsets"):
                checkpoint.evaluate(FakeExtractor({"مصر": [mention]}), [document])

    def test_nonfinite_confidence_cannot_enter_json(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            checkpoint.mention_rows([ExtractedMention("مصر", 0, 3, "location", float("nan"))], "مصر")

    def test_empty_predictions_are_valid_and_empty_corpus_has_zero_metrics(self):
        extractor = FakeExtractor()
        result = checkpoint.extractor_report(extractor, [], checkpoint.evaluate(extractor, []), settings={})
        self.assertEqual(result["overall"]["precision"], 0.0)
        self.assertEqual(result["overall"]["recall"], 0.0)
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["status"], "evaluated")

    def test_mismatched_gold_or_incomplete_pairs_are_rejected(self):
        document = EvalDocument("مصر", (ExtractedMention("مصر", 0, 3, "location"),))
        empty = checkpoint.evaluate(FakeExtractor(), [])
        with self.assertRaisesRegex(ValueError, "every evaluation document"):
            checkpoint.extractor_report(FakeExtractor(), [document], empty, settings={})
        wrong = checkpoint.evaluate(FakeExtractor(), [EvalDocument("مصر", ())])
        with self.assertRaisesRegex(ValueError, "scored gold"):
            checkpoint.extractor_report(FakeExtractor(), [document], wrong, settings={})

    def test_observed_model_errors_are_distinct_from_legitimate_empty_predictions(self):
        logger = logging.getLogger("extract.model")
        original_handlers = list(logger.handlers)
        original_level, original_propagate = logger.level, logger.propagate
        documents = [
            EvalDocument("مصر", (ExtractedMention("مصر", 0, 3, "location"),)),
            EvalDocument("", ()),
        ]
        for _ in range(2):
            errors = []
            extractor = LoggingErrorExtractor()
            evaluation = checkpoint.evaluate(extractor, documents, inference_errors=errors)
            result = checkpoint.extractor_report(
                extractor, documents, evaluation, settings={}, inference_errors=errors,
            )
            self.assertEqual(errors, [{"document_index": 0, "error_type": "RuntimeError"}])
            self.assertEqual(result["status"], "inference_errors")
            self.assertEqual(result["overall"]["false_negatives"], 1)
            self.assertNotIn("/private/", json.dumps(result))
            self.assertEqual(logger.handlers, original_handlers)
            self.assertEqual((logger.level, logger.propagate), (original_level, original_propagate))

    def test_error_listener_is_removed_when_extraction_raises(self):
        logger = logging.getLogger("extract.model")
        original_handlers = list(logger.handlers)
        with patch.object(FakeExtractor, "extract", side_effect=ValueError("bad")):
            with self.assertRaises(ValueError):
                checkpoint.evaluate(FakeExtractor(), [EvalDocument("x", ())], inference_errors=[])
        self.assertEqual(logger.handlers, original_handlers)

    def test_model_revision_is_observed_or_explicitly_unknown(self):
        model = SimpleNamespace(_pipeline=SimpleNamespace(
            model=SimpleNamespace(config=SimpleNamespace(_commit_hash="a" * 40)),
            tokenizer=SimpleNamespace(init_kwargs={"_commit_hash": "/private/cache"}),
        ))
        self.assertEqual(checkpoint.model_revisions(model), {
            "revision_pinned": False, "model_resolved_commit": "a" * 40,
            "tokenizer_resolved_commit": None,
        })
        self.assertIsNone(checkpoint.model_revisions(FakeExtractor())["model_resolved_commit"])


class CheckpointCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "nested" / "m3.json"

    def run_main(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = checkpoint.main(list(args))
        return status, stdout.getvalue(), stderr.getvalue()

    def read_report(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_skip_model_report_is_complete_deterministic_and_truthful(self):
        with patch.object(checkpoint, "is_available") as availability:
            first = self.run_main("--skip-model", "--out", str(self.output))
            first_bytes = self.output.read_bytes()
            second = self.run_main("--skip-model", "--out", str(self.output))
        availability.assert_not_called()
        self.assertEqual((first[0], second[0]), (0, 0))
        self.assertEqual(first_bytes, self.output.read_bytes())
        report = self.read_report()
        self.assertEqual(report["content_sha256"], content_sha256(report))
        self.assertEqual(report["dataset"]["sha256"], hashlib.sha256(checkpoint.EVAL_PATH.read_bytes()).hexdigest())
        self.assertEqual(report["evidence_status"], "insufficient_real_world_evidence")
        self.assertFalse(report["production_accuracy_supported"])
        self.assertFalse(report["dataset"]["independent_holdout_established"])
        self.assertEqual(report["dataset"]["provenance"], "authored_golden_regression_fixture")
        self.assertEqual(report["dataset"]["document_count"], len(load_eval_set()))
        self.assertEqual(report["dataset"]["gold_span_count"], sum(len(d.mentions) for d in load_eval_set()))
        self.assertEqual(report["extractors"][1]["status"], "skipped")
        self.assertIsNone(report["extractors"][1]["overall"])
        self.assertNotIn(str(checkpoint.REPO_ROOT), first_bytes.decode())
        self.assertNotIn(str(self.output), first_bytes.decode())
        self.assertNotIn("generated_at", report)
        self.assertIn("scripts/m3_checkpoint.py", report["code"]["files_sha256"])
        self.assertIn("config/gazetteer.yaml", report["configuration"]["files_sha256"])

        extractor = report["extractors"][0]
        for field in ("false_positives", "false_negatives"):
            self.assertEqual(extractor["overall"][field], sum(len(row[field]) for row in extractor["evidence"]))
        for document, evidence in zip(report["documents"], extractor["evidence"]):
            self.assertEqual(document["text_sha256"], hashlib.sha256(document["text"].encode()).hexdigest())
            self.assertEqual(document["document_index"], evidence["document_index"])
            for row in document["gold"] + evidence["predicted"] + evidence["false_positives"] + evidence["false_negatives"]:
                self.assertEqual(document["text"][row["start"]:row["end"]], row["text"])

    def test_unavailable_model_is_not_reported_as_zero_quality(self):
        with patch.object(checkpoint, "is_available", return_value=False):
            status, _, _ = self.run_main("--out", str(self.output))
        self.assertEqual(status, 0)
        model = self.read_report()["extractors"][1]
        self.assertEqual(model["status"], "unavailable")
        self.assertEqual(model["reason"], "missing_ml_dependencies")
        self.assertIsNone(model["overall"])

    def test_model_initialization_failure_saves_baseline_and_safe_failure_status(self):
        with patch.object(checkpoint, "is_available", return_value=True):
            with patch.object(checkpoint.ModelExtractor, "__init__", side_effect=OSError("/private/cache")):
                status, _, _ = self.run_main("--out", str(self.output))
        self.assertEqual(status, 2)
        report = self.read_report()
        self.assertEqual(report["extractors"][0]["status"], "evaluated")
        model = report["extractors"][1]
        self.assertEqual(model["status"], "initialization_error")
        self.assertEqual(model["error_type"], "OSError")
        self.assertIsNone(model["overall"])
        self.assertNotIn("/private/", json.dumps(report))

    def test_broken_optional_dependency_still_saves_baseline(self):
        with patch.object(checkpoint, "is_available", side_effect=OSError("/private/broken-library")):
            status, _, _ = self.run_main("--out", str(self.output))
        self.assertEqual(status, 2)
        report = self.read_report()
        self.assertEqual(report["extractors"][0]["status"], "evaluated")
        self.assertEqual(report["extractors"][1]["reason"], "model_availability_check_failed")
        self.assertNotIn("/private/", json.dumps(report))

    def test_model_inference_failure_saves_diagnostic_counts_but_no_verdict(self):
        with patch.object(checkpoint, "is_available", return_value=True):
            with patch.object(checkpoint, "ModelExtractor", LoggingErrorExtractor):
                status, output, _ = self.run_main("--out", str(self.output))
        self.assertEqual(status, 2)
        report = self.read_report()
        model = report["extractors"][1]
        self.assertEqual(model["status"], "inference_errors")
        self.assertEqual(len(model["inference_errors"]), report["dataset"]["document_count"])
        self.assertEqual(model["overall"]["false_negatives"], report["dataset"]["gold_span_count"])
        self.assertIn("diagnostic only; no verdict", output)
        self.assertNotIn("verdict (on this golden fixture only)", output)

    def test_successful_model_report_uses_same_metric_contract(self):
        with patch.object(checkpoint, "is_available", return_value=True):
            with patch.object(checkpoint, "ModelExtractor", FakeExtractor):
                status, output, _ = self.run_main("--out", str(self.output))
        self.assertEqual(status, 0)
        model = self.read_report()["extractors"][1]
        self.assertEqual(model["status"], "evaluated")
        self.assertEqual(model["overall"]["true_positives"], 0)
        self.assertEqual(model["settings"]["aggregation_strategy"], "average")
        self.assertIn("on this golden fixture only", output)

    def test_existing_different_report_is_never_overwritten(self):
        self.output.parent.mkdir(parents=True)
        existing = {"different_report": True}
        existing["content_sha256"] = content_sha256(existing)
        self.output.write_text(json.dumps(existing), encoding="utf-8")
        before = self.output.read_bytes()
        status, _, error = self.run_main("--skip-model", "--out", str(self.output))
        self.assertEqual(status, 2)
        self.assertEqual(self.output.read_bytes(), before)
        self.assertIn("refusing to replace", error)

    def test_changed_inputs_during_run_refuse_to_save(self):
        fingerprints = checkpoint.input_fingerprints()
        changed = {**fingerprints, "config/gazetteer.yaml": "a" * 64}
        with patch.object(checkpoint, "input_fingerprints", side_effect=[fingerprints, changed]):
            status, _, error = self.run_main("--skip-model", "--out", str(self.output))
        self.assertEqual(status, 2)
        self.assertFalse(self.output.exists())
        self.assertIn("changed during the run", error)

    def test_no_out_keeps_console_only_behavior(self):
        status, output, error = self.run_main("--skip-model")
        self.assertEqual(status, 0)
        self.assertIn("OVERALL", output)
        self.assertIn("what the gazetteer got wrong", output)
        self.assertNotIn("wrote evaluation report", output)
        self.assertEqual(error, "")

    def test_direct_invocation_works_outside_checkout_without_pythonpath(self):
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, str(checkpoint.REPO_ROOT / "scripts/m3_checkpoint.py"),
             "--skip-model", "--out", str(self.output)],
            cwd=self.temporary.name, env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_report()["extractors"][0]["status"], "evaluated")

    def test_source_archive_without_git_has_explicit_unknown_revision(self):
        with patch.object(checkpoint.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(checkpoint.code_revision(checkpoint.CODE_PATHS), {
                "git_commit": None, "evaluation_inputs_dirty": None,
            })


if __name__ == "__main__":
    unittest.main()
