"""Tests for the M4.1 production-label learning evidence foundation."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.resolve.blocking import KeyBlocker
from src.resolve.features import MentionContext
from src.resolve.learning import (
    LearningContractError,
    LabeledPair,
    SamplingCandidate,
    SamplingPlan,
    assert_training_audit_disjoint,
    blocking_recall,
    candidates_from_blocker,
    evaluate_candidate,
    freeze_label_dataset,
    freeze_scorer_artifact,
    grouped_split,
    load_scorer_artifact,
    sample_blocking_survivors,
    validate_label_dataset,
    validate_sampling_manifest,
    validate_scorer_artifact,
    write_immutable_json,
)
from src.resolve.scorer import PairScorer


FEATURES = ["name_similarity"]
MANIFEST_DIGEST = "a" * 64
BLOCKING_VERSION = "key-blocker-v1"


def _sampling(stratum: str = "uncertain") -> dict:
    return {
        "stratum": stratum,
        "reason": "fixture_reason",
        "sampling_probability": 0.5,
    }


def _pair(
    pair_id: str,
    group_id: str,
    label: bool | None,
    *,
    feature: float = 1.0,
    exact: bool = False,
    gazetteer: bool = False,
) -> dict:
    return {
        "pair_id": pair_id,
        "group_id": group_id,
        "source_group": "source-a | source-b",
        "label": label,
        "feature_vector": [feature],
        "baseline_exact_match": exact,
        "baseline_gazetteer_match": gazetteer,
        "sampling": _sampling(),
    }


def _dataset(
    purpose: str,
    dataset_id: str,
    pairs: list[dict],
    *,
    universe_id: str = "mention-snapshot-v1",
):
    kwargs = {
        "dataset_id": dataset_id,
        "purpose": purpose,
        "candidate_universe_id": universe_id,
        "pairs": pairs,
        "feature_names": FEATURES if purpose != "blocking_recall_gold" else ["pair_id_only"],
        "blocking_version": BLOCKING_VERSION,
    }
    if purpose != "blocking_recall_gold":
        kwargs.update(
            {
                "sampling_manifest_id": "sampling-v1",
                "sampling_manifest_sha256": MANIFEST_DIGEST,
            }
        )
    return validate_label_dataset(freeze_label_dataset(**kwargs))


def _gold_pair(pair_id: str, group_id: str, label: bool) -> dict:
    return {
        "pair_id": pair_id,
        "group_id": group_id,
        "source_group": "source-a | source-b",
        "label": label,
        "feature_vector": [],
    }


def _artifact(training, audit):
    raw = freeze_scorer_artifact(
        artifact_id="pair-scorer-candidate-v2",
        feature_names=FEATURES,
        coefficients=[8.0],
        intercept=-4.0,
        threshold=0.5,
        created_at="2026-08-21T00:00:00+00:00",
        training_dataset_id=training.dataset_id,
        training_label_snapshot_sha256=training.content_digest,
        blocking_version=BLOCKING_VERSION,
        code_commit="b" * 40,
        random_seed=20260821,
        evaluation_set_id=audit.dataset_id,
        metrics={
            "precision": 0.0,
            "recall": 0.0,
            "pr_auc": 0.0,
            "calibration": [],
            "model_originated_merges": 0,
        },
    )
    return validate_scorer_artifact(raw)


def test_sampling_is_deterministic_records_strata_and_caps_frequent_names():
    candidates = [
        SamplingCandidate("u-1", "frequent", "one", "A", "A", 0.60),
        SamplingCandidate("u-2", "frequent", "two", "B", "B", 0.58),
        SamplingCandidate("h-1", "high-one", "h", "C", "C", 0.95),
        SamplingCandidate("l-1", "low-one", "l", "D", "D", 0.05),
        SamplingCandidate("d-1", "diverse-one", "d", "E", "E", 0.40),
    ]
    plan = SamplingPlan(
        manifest_id="review-sample-v1",
        candidate_universe_id="mentions-v1",
        blocker_name="key_blocker",
        blocking_version=BLOCKING_VERSION,
        threshold=0.60,
        seed=7,
        quotas={"uncertain": 2, "high_score": 1, "low_score": 1, "diverse_name": 1},
        max_per_name=1,
        max_per_source=2,
    )

    first = sample_blocking_survivors(candidates, plan)
    second = sample_blocking_survivors(candidates, plan)

    assert first == second
    validate_sampling_manifest(first)
    selected = first["sampled_pairs"]
    assert {row["sampling"]["stratum"] for row in selected} == {
        "uncertain",
        "high_score",
        "low_score",
        "diverse_name",
    }
    # At most one of the two candidates containing the frequent normalized
    # name reaches the diversity-capped sampling frame.
    assert sum("frequent" in (row["left_group"], row["right_group"]) for row in selected) == 1
    for row in selected:
        metadata = row["sampling"]
        assert metadata["sampling_probability_basis"].startswith("realized deterministic")
        assert metadata["stratum_population"] >= metadata["sampling_frame_population"]
        assert 0.0 < metadata["sampling_probability"] <= 1.0


def test_diversity_caps_apply_across_the_full_manifest_and_explain_shortfalls():
    plan = SamplingPlan(
        manifest_id="global-cap-v1",
        candidate_universe_id="mentions-v1",
        blocker_name="key_blocker",
        blocking_version=BLOCKING_VERSION,
        threshold=0.60,
        seed=7,
        quotas={"uncertain": 1, "high_score": 1, "low_score": 1, "diverse_name": 1},
        max_per_name=1,
        max_per_source=1,
    )
    manifest = sample_blocking_survivors(
        [
            # The fixed stratum priority selects this first. Its name and
            # source must then be unavailable in all remaining strata.
            SamplingCandidate("uncertain-frequent", "frequent", "u", "shared", "u-source", 0.60),
            SamplingCandidate("high-frequent", "frequent", "h", "high", "h-source", 0.95),
            SamplingCandidate("low-shared-source", "low", "l", "shared", "low-source", 0.05),
            SamplingCandidate("diverse-unique", "diverse", "d", "diverse-source", "d-source", 0.40),
        ],
        plan,
    )

    selected = manifest["sampled_pairs"]
    assert sum("frequent" in (row["left_group"], row["right_group"]) for row in selected) == 1
    assert sum("shared" in (row["left_source"], row["right_source"]) for row in selected) == 1
    assert {row["pair_id"] for row in selected} == {"uncertain-frequent", "diverse-unique"}
    summary = {row["stratum"]: row for row in manifest["strata"]}
    assert summary["high_score"]["quota_shortfall"] == 1
    assert summary["high_score"]["quota_shortfall_due_to_global_caps"] == 1
    assert summary["high_score"]["quota_shortfall_reason"] == "global_diversity_caps"
    assert summary["low_score"]["quota_shortfall_reason"] == "global_diversity_caps"
    assert all(
        row["sampling"]["global_diversity_cap_scope"] == "full_manifest" for row in selected
    )


def test_candidate_adapter_only_exports_pairs_that_survived_existing_blocking():
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    contexts = {
        1: MentionContext(1, "same", "person", 1, "BBCArabic", now, frozenset({"last:s"}), frozenset()),
        2: MentionContext(2, "same", "person", 2, "CNNArabic", now, frozenset({"last:s"}), frozenset()),
        3: MentionContext(3, "other", "person", 3, "BBCArabic", now, frozenset({"last:o"}), frozenset()),
        4: MentionContext(4, "same", "location", 4, "BBCArabic", now, frozenset({"last:s"}), frozenset()),
    }

    candidates = candidates_from_blocker(contexts, KeyBlocker(), PairScorer())

    assert [candidate.pair_id for candidate in candidates] == ["1:2"]
    assert candidates[0].baseline_exact_match is True
    assert len(candidates[0].feature_vector) == 6


def test_sampling_manifest_and_frozen_file_detect_tampering(tmp_path):
    plan = SamplingPlan(
        manifest_id="review-sample-v1",
        candidate_universe_id="mentions-v1",
        blocker_name="key_blocker",
        blocking_version=BLOCKING_VERSION,
        threshold=0.60,
        seed=7,
        quotas={"uncertain": 1},
    )
    manifest = sample_blocking_survivors(
        [SamplingCandidate("pair-1", "a", "b", "A", "B", 0.60)], plan
    )
    path = tmp_path / "manifest.json"
    write_immutable_json(path, manifest)
    assert json.loads(path.read_text(encoding="utf-8"))["content_sha256"] == manifest["content_sha256"]

    tampered = dict(manifest)
    tampered["sampled_pairs"] = [dict(manifest["sampled_pairs"][0], score=0.0)]
    with pytest.raises(LearningContractError, match="content_sha256"):
        validate_sampling_manifest(tampered)
    with pytest.raises(LearningContractError, match="refusing to replace"):
        write_immutable_json(path, tampered)


def test_frozen_audit_cannot_overlap_training_by_pair_or_group():
    training = _dataset("training", "training-v1", [_pair("train-1", "محمد | احمد", True)])
    audit_group_leak = _dataset("audit_holdout", "audit-v1", [_pair("audit-1", "محمد | احمد", False)])
    with pytest.raises(LearningContractError, match="normalized-name/entity groups"):
        assert_training_audit_disjoint(training, audit_group_leak)

    audit_pair_leak = _dataset("audit_holdout", "audit-v2", [_pair("train-1", "other | group", False)])
    with pytest.raises(LearningContractError, match="pair ids"):
        assert_training_audit_disjoint(training, audit_pair_leak)


def test_grouped_split_is_deterministic_and_never_leaks_a_name_group():
    pairs = tuple(
        LabeledPair(
            pair_id=f"p-{index}",
            group_id=f"group-{index // 2}",
            source_group="source",
            label=True,
            feature_vector=(1.0,),
            baseline_exact_match=False,
            baseline_gazetteer_match=False,
            sampling=_sampling(),
        )
        for index in range(8)
    )
    first = grouped_split(pairs, test_fraction=0.5, seed=42)
    second = grouped_split(pairs, test_fraction=0.5, seed=42)
    assert first == second
    assert not set(first.train_groups) & set(first.test_groups)
    train_groups = {pair.group_id for pair in pairs if pair.pair_id in first.train_pair_ids}
    test_groups = {pair.group_id for pair in pairs if pair.pair_id in first.test_pair_ids}
    assert not train_groups & test_groups


def test_blocking_recall_uses_pre_blocking_gold_not_a_review_queue():
    # Only 1:2 shares a key.  1:3 is a true pair that the blocker discarded,
    # precisely the failure an after-blocking audit set cannot reveal.
    candidates = KeyBlocker().candidate_pairs({1: {"last:one"}, 2: {"last:one"}, 3: {"last:three"}})
    survivor_ids = {f"{left}:{right}" for left, right in candidates}
    gold = _dataset(
        "blocking_recall_gold",
        "blocking-gold-v1",
        [
            _gold_pair("1:2", "one", True),
            _gold_pair("1:3", "one", True),
            _gold_pair("2:3", "different", False),
        ],
    )

    result = blocking_recall(gold, survivor_ids, target=0.95, min_positive_support=3)
    assert result.positive_gold_pairs == 2
    assert result.surviving_positive_pairs == 1
    assert result.recall == pytest.approx(0.5)
    assert result.support_sufficient is False


def test_future_artifact_requires_metadata_and_rejects_the_legacy_live_weights(tmp_path):
    training = _dataset("training", "training-v1", [_pair("train-1", "training-group", True)])
    audit = _dataset("audit_holdout", "audit-v1", [_pair("audit-1", "audit-group", False)])
    artifact = _artifact(training, audit)
    assert artifact.feature_names == ("name_similarity",)

    bad = freeze_scorer_artifact(
        artifact_id="candidate-v3",
        feature_names=FEATURES,
        coefficients=[1.0],
        intercept=0.0,
        threshold=0.5,
        created_at="2026-08-21T00:00:00+00:00",
        training_dataset_id=training.dataset_id,
        training_label_snapshot_sha256=training.content_digest,
        blocking_version=BLOCKING_VERSION,
        code_commit="c" * 40,
        random_seed=1,
        evaluation_set_id=audit.dataset_id,
        metrics={
            "precision": 0.0,
            "recall": 0.0,
            "pr_auc": 0.0,
            "calibration": [],
            "model_originated_merges": 0,
        },
    )
    bad["feature_schema_sha256"] = "0" * 64
    with pytest.raises(LearningContractError, match="content_sha256"):
        validate_scorer_artifact(bad)

    repo = Path(__file__).resolve().parents[2]
    with pytest.raises(LearningContractError, match="schema_version"):
        load_scorer_artifact(repo / "config" / "pair_scorer_weights.json")


def test_report_says_not_proven_when_support_is_too_small():
    training = _dataset("training", "training-v1", [_pair("train-1", "training-group", True)])
    audit = _dataset("audit_holdout", "audit-v1", [_pair("audit-1", "audit-group", True)])
    gold = _dataset("blocking_recall_gold", "blocking-gold-v1", [_gold_pair("audit-1", "audit-group", True)])
    report = evaluate_candidate(
        audit=audit,
        blocking_gold=gold,
        blocking_surviving_pair_ids=["audit-1"],
        artifact=_artifact(training, audit),
        training=training,
    )

    assert report["status"] == "not_proven"
    assert report["deployment_action"] == "keep_current_weights"
    assert report["metrics"]["candidate"]["precision"] == 1.0
    assert any("need 60" in reason for reason in report["not_proven_reasons"])


def test_reproducible_sample_cli_writes_a_manifest(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    candidates = tmp_path / "candidates.json"
    plan = tmp_path / "plan.json"
    out = tmp_path / "manifest.json"
    candidates.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "pair_id": "p-1",
                        "left_group": "a",
                        "right_group": "b",
                        "left_source": "A",
                        "right_source": "B",
                        "score": 0.6,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    plan.write_text(
        json.dumps(
            {
                "manifest_id": "sample-cli-v1",
                "candidate_universe_id": "mentions-v1",
                "blocker_name": "key_blocker",
                "blocking_version": BLOCKING_VERSION,
                "threshold": 0.6,
                "seed": 42,
                "quotas": {"uncertain": 1},
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_pair_scorer.py",
            "sample",
            "--candidates",
            str(candidates),
            "--plan",
            str(plan),
            "--out",
            str(out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = json.loads(out.read_text(encoding="utf-8"))
    validate_sampling_manifest(manifest)
    assert manifest["sampled_pairs"][0]["pair_id"] == "p-1"


def test_reproducible_report_cli_keeps_current_weights_when_evidence_is_small(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    training = _dataset("training", "training-v1", [_pair("train-1", "training-group", True)])
    audit = _dataset("audit_holdout", "audit-v1", [_pair("audit-1", "audit-group", True)])
    gold = _dataset("blocking_recall_gold", "blocking-gold-v1", [_gold_pair("audit-1", "audit-group", True)])
    artifact = freeze_scorer_artifact(
        artifact_id="pair-scorer-candidate-v2",
        feature_names=FEATURES,
        coefficients=[8.0],
        intercept=-4.0,
        threshold=0.5,
        created_at="2026-08-21T00:00:00+00:00",
        training_dataset_id=training.dataset_id,
        training_label_snapshot_sha256=training.content_digest,
        blocking_version=BLOCKING_VERSION,
        code_commit="b" * 40,
        random_seed=20260821,
        evaluation_set_id=audit.dataset_id,
        metrics={
            "precision": 0.0,
            "recall": 0.0,
            "pr_auc": 0.0,
            "calibration": [],
            "model_originated_merges": 0,
        },
    )
    paths = {
        "training": tmp_path / "training.json",
        "audit": tmp_path / "audit.json",
        "gold": tmp_path / "gold.json",
        "artifact": tmp_path / "artifact.json",
        "survivors": tmp_path / "survivors.json",
        "out": tmp_path / "report.json",
    }
    # The parsed Dataset dataclass deliberately exposes its verified digest,
    # but the CLI needs the original frozen JSON files.
    raw_training = freeze_label_dataset(
        dataset_id="training-v1",
        purpose="training",
        candidate_universe_id="mention-snapshot-v1",
        pairs=[_pair("train-1", "training-group", True)],
        feature_names=FEATURES,
        sampling_manifest_id="sampling-v1",
        sampling_manifest_sha256=MANIFEST_DIGEST,
        blocking_version=BLOCKING_VERSION,
    )
    raw_audit = freeze_label_dataset(
        dataset_id="audit-v1",
        purpose="audit_holdout",
        candidate_universe_id="mention-snapshot-v1",
        pairs=[_pair("audit-1", "audit-group", True)],
        feature_names=FEATURES,
        sampling_manifest_id="sampling-v1",
        sampling_manifest_sha256=MANIFEST_DIGEST,
        blocking_version=BLOCKING_VERSION,
    )
    raw_gold = freeze_label_dataset(
        dataset_id="blocking-gold-v1",
        purpose="blocking_recall_gold",
        candidate_universe_id="mention-snapshot-v1",
        pairs=[_gold_pair("audit-1", "audit-group", True)],
        feature_names=["pair_id_only"],
        blocking_version=BLOCKING_VERSION,
    )
    for key, raw in (("training", raw_training), ("audit", raw_audit), ("gold", raw_gold), ("artifact", artifact)):
        paths[key].write_text(json.dumps(raw), encoding="utf-8")
    paths["survivors"].write_text(
        json.dumps({"candidate_universe_id": "mention-snapshot-v1", "pair_ids": ["audit-1"]}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_pair_scorer.py",
            "report",
            "--training",
            str(paths["training"]),
            "--audit",
            str(paths["audit"]),
            "--blocking-gold",
            str(paths["gold"]),
            "--blocking-survivors",
            str(paths["survivors"]),
            "--artifact",
            str(paths["artifact"]),
            "--out",
            str(paths["out"]),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(paths["out"].read_text(encoding="utf-8"))
    assert report["status"] == "not_proven"
    assert report["deployment_action"] == "keep_current_weights"
