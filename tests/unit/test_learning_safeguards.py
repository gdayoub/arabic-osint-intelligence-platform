"""Regression tests for honest scorer metrics and leakage-safe evaluation.

These tests use unittest so the pure-Python learning contract can also be
checked without the optional database/test dependencies:

    python -m unittest tests.unit.test_learning_safeguards -v

The normal pytest unit suite discovers the same tests.
"""

from __future__ import annotations

import itertools
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from src.resolve.learning import (
    EvaluationPolicy,
    LearningContractError,
    SamplingCandidate,
    SamplingPlan,
    _average_precision,
    _binary_metrics,
    assert_training_audit_disjoint,
    evaluate_candidate,
    freeze_label_dataset,
    freeze_scorer_artifact,
    grouped_split,
    load_label_dataset,
    sample_blocking_survivors,
    validate_label_dataset,
    validate_scorer_artifact,
)


def _row(pair_id, group_id, left=None, right=None, *, label=True):
    row = {
        "pair_id": pair_id,
        "group_id": group_id,
        "source_group": "test-source",
        "label": label,
        "feature_vector": [1.0],
        "sampling": {
            "stratum": "uncertain",
            "reason": "regression_fixture",
            "sampling_probability": 0.5,
        },
    }
    if left is not None:
        row["left_group"] = left
    if right is not None:
        row["right_group"] = right
    return row


def _snapshot(purpose, rows):
    return freeze_label_dataset(
        dataset_id=f"{purpose}-fixture",
        purpose=purpose,
        candidate_universe_id="fixture-universe",
        pairs=rows,
        feature_names=["name_similarity"],
        sampling_manifest_id="fixture-manifest",
        sampling_manifest_sha256="a" * 64,
        blocking_version="fixture-blocker-v1",
    )


def _dataset(purpose, rows):
    return validate_label_dataset(_snapshot(purpose, rows))


def _report(training, audit):
    artifact = validate_scorer_artifact(freeze_scorer_artifact(
        artifact_id="fixture-artifact",
        feature_names=audit.feature_names,
        coefficients=[0.0],
        intercept=0.0,
        threshold=0.5,
        created_at="2026-10-12T00:00:00+00:00",
        training_dataset_id=training.dataset_id,
        training_label_snapshot_sha256=training.content_digest,
        blocking_version="fixture-blocker-v1",
        code_commit="b" * 40,
        random_seed=42,
        evaluation_set_id=audit.dataset_id,
        metrics={
            "precision": 0.0,
            "recall": 0.0,
            "pr_auc": 0.0,
            "calibration": [],
            "model_originated_merges": 0,
        },
    ))
    gold = validate_label_dataset(freeze_label_dataset(
        dataset_id="blocking-gold-fixture",
        purpose="blocking_recall_gold",
        candidate_universe_id="fixture-universe",
        pairs=[{"pair_id": "gold", "group_id": "gold-group", "label": True}],
        feature_names=["pair_id_only"],
    ))
    return evaluate_candidate(
        audit=audit,
        blocking_gold=gold,
        blocking_surviving_pair_ids=["gold"],
        artifact=artifact,
        training=training,
        policy=EvaluationPolicy(bootstrap_samples=20),
    )


class AveragePrecisionTests(unittest.TestCase):
    def test_mixed_label_tie_is_independent_of_row_order(self):
        self.assertEqual(_average_precision([True, False], [0.5, 0.5]), 0.5)
        self.assertEqual(_average_precision([False, True], [0.5, 0.5]), 0.5)

    def test_multiple_score_ties_use_threshold_precision(self):
        rows = [(0.9, True), (0.9, False), (0.5, True), (0.5, False), (0.1, True)]
        # Each distinct threshold adds 1/3 recall. Precision is 1/2, 2/4,
        # and 3/5 at those thresholds, giving AP = 8/15.
        for ordering in itertools.permutations(rows):
            with self.subTest(ordering=ordering):
                scores, labels = zip(*ordering)
                self.assertAlmostEqual(_average_precision(labels, scores), 8 / 15)

    def test_all_tied_scores_equal_positive_prevalence(self):
        self.assertEqual(_average_precision([True, False, True, False], [0.0] * 4), 0.5)
        self.assertEqual(_average_precision([False, False, True, False], [1.0] * 4), 0.25)

    def test_untied_scores_keep_the_existing_definition(self):
        self.assertEqual(_average_precision([True, False], [0.8, 0.2]), 1.0)
        self.assertEqual(_average_precision([False, True], [0.8, 0.2]), 0.5)
        self.assertAlmostEqual(_average_precision([True, False, True], [0.8, 0.5, 0.2]), 5 / 6)

    def test_empty_or_no_positive_labels_remain_unmeasurable(self):
        self.assertIsNone(_average_precision([], []))
        self.assertIsNone(_average_precision([False, False], [0.9, 0.1]))

    def test_all_positive_labels_have_perfect_average_precision(self):
        self.assertEqual(_average_precision([True, True, True], [0.9, 0.5, 0.5]), 1.0)

    def test_binary_baseline_metrics_handle_zero_and_one_score_ties(self):
        labels = [True, False, True, False]
        predictions = [True, True, False, False]
        scores = [float(value) for value in predictions]
        metrics = _binary_metrics(labels, scores, predictions)
        reversed_metrics = _binary_metrics(labels[::-1], scores[::-1], predictions[::-1])
        self.assertEqual(metrics.pr_auc, 0.5)
        self.assertEqual(reversed_metrics.pr_auc, 0.5)
        self.assertEqual(metrics.true_positive, 1)
        self.assertEqual(metrics.false_positive, 1)
        self.assertEqual(metrics.recall, 0.5)

    def test_public_report_uses_tie_safe_ap_for_candidate_and_baselines(self):
        training = _dataset("training", [_row("train", "train-group", "T", "U")])
        audit_rows = [
            _row("audit-1", "audit-group-1", "A", "B", label=True),
            _row("audit-2", "audit-group-2", "C", "D", label=False),
        ]
        for rows in (audit_rows, audit_rows[::-1]):
            report = _report(training, _dataset("audit_holdout", rows))
            for method in ("candidate", "exact_match_baseline", "gazetteer_baseline"):
                self.assertEqual(report["metrics"][method]["pr_auc"], 0.5)


class EndpointMetadataTests(unittest.TestCase):
    def test_existing_manifest_endpoints_survive_freezing_and_parsing(self):
        manifest = sample_blocking_survivors(
            [SamplingCandidate("pair", "Alice", "Bob", "S", "S", 0.5, (1.0,))],
            SamplingPlan(
                manifest_id="fixture-manifest",
                candidate_universe_id="fixture-universe",
                blocker_name="key_blocker",
                blocking_version="fixture-blocker-v1",
                threshold=0.5,
                seed=42,
                quotas={"uncertain": 1},
            ),
        )
        row = dict(manifest["sampled_pairs"][0], label=True)
        raw = _snapshot("training", [row])
        pair = validate_label_dataset(raw).pairs[0]
        self.assertEqual(raw["schema_version"], "1.0.0")
        self.assertEqual(pair.group_id, "Alice | Bob")
        self.assertEqual(pair.left_group, "Alice")
        self.assertEqual(pair.right_group, "Bob")
        self.assertEqual(pair.name_groups, ("Alice", "Bob"))

    def test_legacy_version_one_snapshot_still_loads_without_rewriting_its_hash(self):
        raw = _snapshot("training", [_row("legacy", "Alice | Bob")])
        encoded = json.dumps(raw).encode("utf-8")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_bytes(encoded)
            loaded = load_label_dataset(path)
            self.assertEqual(path.read_bytes(), encoded)
        self.assertEqual(loaded.content_digest, raw["content_sha256"])
        self.assertEqual(loaded.pairs[0].group_id, "Alice | Bob")
        self.assertIsNone(loaded.pairs[0].left_group)
        self.assertIsNone(loaded.pairs[0].right_group)
        with self.assertRaisesRegex(LearningContractError, "Re-export endpoint groups"):
            _ = loaded.pairs[0].name_groups

    def test_partial_or_invalid_endpoint_metadata_is_rejected(self):
        invalid_metadata = [
            {"left_group": "A"},
            {"right_group": "B"},
            {"left_group": None, "right_group": None},
            {"left_group": "", "right_group": "B"},
            {"left_group": "A", "right_group": " "},
            {"left_group": 1, "right_group": "B"},
            {"left_group": "A", "right_group": ["B"]},
        ]
        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                row = dict(_row("pair", "opaque"), **metadata)
                with self.assertRaisesRegex(LearningContractError, "pairs.(left|right)_group"):
                    _snapshot("training", [row])

    def test_literal_delimiters_in_endpoint_names_are_never_parsed(self):
        training = _dataset("training", [_row("train", "one", "Alice | Bob", "C")])
        audit = _dataset("audit_holdout", [_row("audit", "two", "Alice", "D")])
        self.assertEqual(training.pairs[0].name_groups, ("Alice | Bob", "C"))
        assert_training_audit_disjoint(training, audit)

    def test_same_name_on_both_endpoints_is_deduplicated(self):
        pair = _dataset("training", [_row("pair", "opaque", "Alice", "Alice")]).pairs[0]
        self.assertEqual(pair.name_groups, ("Alice",))


class AuditDisjointnessTests(unittest.TestCase):
    def test_shared_endpoint_is_rejected_in_either_orientation(self):
        training = _dataset("training", [_row("train", "Alice | Bob", "Alice", "Bob")])
        for left, right in (("Alice", "Carol"), ("Carol", "Alice"), ("Bob", "Carol"), ("Carol", "Bob")):
            with self.subTest(left=left, right=right):
                audit = _dataset("audit_holdout", [_row("audit", "other", left, right)])
                with self.assertRaisesRegex(LearningContractError, "normalized-name/entity groups"):
                    assert_training_audit_disjoint(training, audit)

    def test_unresolved_rows_also_count_for_leakage(self):
        training = _dataset("training", [_row("train", "one", "Alice", "Bob", label=None)])
        audit = _dataset("audit_holdout", [_row("audit", "two", "Alice", "Carol")])
        with self.assertRaisesRegex(LearningContractError, "normalized-name/entity groups"):
            assert_training_audit_disjoint(training, audit)

    def test_opaque_group_and_pair_id_guards_are_preserved(self):
        training = _dataset("training", [_row("train", "shared", "A", "B")])
        group_overlap = _dataset("audit_holdout", [_row("audit", "shared", "C", "D")])
        pair_overlap = _dataset("audit_holdout", [_row("train", "other", "C", "D")])
        with self.assertRaisesRegex(LearningContractError, "normalized-name/entity groups"):
            assert_training_audit_disjoint(training, group_overlap)
        with self.assertRaisesRegex(LearningContractError, "pair ids"):
            assert_training_audit_disjoint(training, pair_overlap)

    def test_disjoint_endpoints_and_opaque_groups_are_allowed(self):
        training = _dataset("training", [_row("train", "Alice", "B", "C")])
        audit = _dataset("audit_holdout", [_row("audit", "other", "Alice", "D")])
        # Opaque group IDs and endpoint IDs are separate namespaces.
        assert_training_audit_disjoint(training, audit)

    def test_legacy_missing_endpoints_fail_closed_on_either_side(self):
        for missing_side in ("training", "audit_holdout"):
            with self.subTest(missing_side=missing_side):
                train_row = _row("train", "Alice | Bob", "Alice", "Bob")
                audit_row = _row("audit", "Carol | Dana", "Carol", "Dana")
                row = train_row if missing_side == "training" else audit_row
                del row["left_group"]
                del row["right_group"]
                training = _dataset("training", [train_row])
                audit = _dataset("audit_holdout", [audit_row])
                with self.assertRaisesRegex(LearningContractError, "disjointness is not proven"):
                    assert_training_audit_disjoint(training, audit)

    def test_public_report_keeps_current_weights_for_legacy_or_leaking_data(self):
        cases = [
            (_row("train", "Alice | Bob"), _row("audit", "Alice | Carol"), "Re-export"),
            (_row("train", "one", "Alice", "Bob"), _row("audit", "two", "Alice", "Carol"), "share normalized"),
        ]
        for train_row, audit_row, reason in cases:
            with self.subTest(reason=reason):
                report = _report(_dataset("training", [train_row]), _dataset("audit_holdout", [audit_row]))
                self.assertEqual(report["status"], "not_proven")
                self.assertEqual(report["deployment_action"], "keep_current_weights")
                self.assertTrue(any(reason in text for text in report["not_proven_reasons"]))


class ConnectedSplitTests(unittest.TestCase):
    def setUp(self):
        self.pairs = _dataset("training", [
            _row("ab", "group-ab", "A", "B"),
            _row("bc", "group-bc", "B", "C"),
            _row("cd", "group-cd", "C", "D"),
            _row("ef", "group-ef", "E", "F"),
        ]).pairs

    def test_transitively_connected_endpoints_never_cross_the_split(self):
        for seed in range(25):
            with self.subTest(seed=seed):
                split = grouped_split(self.pairs, test_fraction=0.5, seed=seed)
                train_ids = set(split.train_pair_ids)
                test_ids = set(split.test_pair_ids)
                self.assertEqual(train_ids | test_ids, {"ab", "bc", "cd", "ef"})
                self.assertFalse(train_ids & test_ids)
                self.assertIn({"ab", "bc", "cd"}, [train_ids, test_ids])
                train_names = {name for pair in self.pairs if pair.pair_id in train_ids for name in pair.name_groups}
                test_names = {name for pair in self.pairs if pair.pair_id in test_ids for name in pair.name_groups}
                self.assertFalse(train_names & test_names)
                self.assertEqual(set(split.train_groups), {pair.group_id for pair in self.pairs if pair.pair_id in train_ids})
                self.assertEqual(set(split.test_groups), {pair.group_id for pair in self.pairs if pair.pair_id in test_ids})

    def test_split_is_stable_under_row_and_endpoint_permutations(self):
        expected = grouped_split(self.pairs, test_fraction=0.5, seed=42)
        for ordering in itertools.permutations(self.pairs):
            self.assertEqual(grouped_split(ordering, test_fraction=0.5, seed=42), expected)
        reversed_endpoints = tuple(replace(pair, left_group=pair.right_group, right_group=pair.left_group) for pair in self.pairs)
        self.assertEqual(grouped_split(reversed_endpoints, test_fraction=0.5, seed=42), expected)

    def test_opaque_group_constraint_is_preserved_alongside_endpoint_connections(self):
        pairs = _dataset("training", [
            _row("ab", "shared-opaque", "A", "B"),
            _row("cd", "shared-opaque", "C", "D"),
            _row("de", "next-opaque", "D", "E"),
            _row("fg", "independent-opaque", "F", "G"),
        ]).pairs
        split = grouped_split(pairs, test_fraction=0.5, seed=42)
        self.assertIn({"ab", "cd", "de"}, [set(split.train_pair_ids), set(split.test_pair_ids)])

    def test_one_connected_component_cannot_be_split_safely(self):
        with self.assertRaisesRegex(LearningContractError, "two disjoint name/entity components"):
            grouped_split(self.pairs[:3], test_fraction=0.5, seed=42)

    def test_legacy_group_only_rows_cannot_claim_a_group_safe_split(self):
        pairs = _dataset("training", [_row("ab", "Alice | Bob"), _row("ac", "Alice | Carol")]).pairs
        with self.assertRaisesRegex(LearningContractError, "Re-export endpoint groups"):
            grouped_split(pairs, test_fraction=0.5, seed=42)

    def test_duplicate_pair_ids_are_rejected(self):
        pairs = self.pairs + (replace(self.pairs[-1], pair_id="ab"),)
        with self.assertRaisesRegex(LearningContractError, "duplicate pair_id"):
            grouped_split(pairs, test_fraction=0.5, seed=42)

    def test_fraction_applies_to_components_and_keeps_both_partitions_nonempty(self):
        for fraction in (0.01, 0.5, 0.99):
            split = grouped_split(self.pairs, test_fraction=fraction, seed=42)
            self.assertTrue(split.train_pair_ids)
            self.assertTrue(split.test_pair_ids)
        for fraction in (0.0, 1.0, -0.1, 1.1, float("nan")):
            with self.assertRaisesRegex(LearningContractError, "strictly between"):
                grouped_split(self.pairs, test_fraction=fraction, seed=42)


if __name__ == "__main__":
    unittest.main()
