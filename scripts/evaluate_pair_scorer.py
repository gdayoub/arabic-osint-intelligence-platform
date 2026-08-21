"""Build a reproducible M4.1 sampling manifest or scorer evidence report.

This script intentionally has no option that replaces the live scorer weight
file.  Its job is to make the evidence for a future model reviewable first.

Examples:

    python scripts/evaluate_pair_scorer.py sample \
      --candidates work/blocking-survivors.json \
      --plan work/review-sampling-plan.json \
      --out work/review-sampling-manifest.json

    python scripts/evaluate_pair_scorer.py report \
      --training data/labels/training-v1.json \
      --audit data/labels/audit-v1.json \
      --blocking-gold data/labels/blocking-gold-v1.json \
      --blocking-survivors work/blocking-survivors-ids.json \
      --artifact artifacts/pair-scorer-v2.json \
      --out reports/pair-scorer-v2.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

# Make the documented ``python scripts/...`` command work from a checkout,
# without asking a reviewer to remember a PYTHONPATH environment variable.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.resolve.learning import (
    LearningContractError,
    SamplingCandidate,
    SamplingPlan,
    evaluate_candidate,
    load_label_dataset,
    load_scorer_artifact,
    sample_blocking_survivors,
    validate_sampling_manifest,
    write_immutable_json,
)


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningContractError(f"cannot read {label} {path}: {exc}") from exc


def _candidate_from_json(raw: Mapping[str, Any]) -> SamplingCandidate:
    vector = raw.get("feature_vector", [])
    if not isinstance(vector, list):
        raise LearningContractError("candidate feature_vector must be a list")
    return SamplingCandidate(
        pair_id=raw.get("pair_id"),
        left_group=raw.get("left_group"),
        right_group=raw.get("right_group"),
        left_source=raw.get("left_source"),
        right_source=raw.get("right_source"),
        score=raw.get("score"),
        feature_vector=tuple(vector),
        baseline_exact_match=raw.get("baseline_exact_match", False),
        baseline_gazetteer_match=raw.get("baseline_gazetteer_match", False),
    )


def _load_candidates(path: Path) -> tuple[SamplingCandidate, ...]:
    raw = _read_json(path, "candidate pairs")
    if isinstance(raw, Mapping):
        raw = raw.get("candidates")
    if not isinstance(raw, list):
        raise LearningContractError("candidate file must be a list or an object with a candidates list")
    if not all(isinstance(item, Mapping) for item in raw):
        raise LearningContractError("every candidate must be an object")
    return tuple(_candidate_from_json(item) for item in raw)


def _load_plan(path: Path) -> SamplingPlan:
    raw = _read_json(path, "sampling plan")
    if not isinstance(raw, Mapping):
        raise LearningContractError("sampling plan must be an object")
    allowed = {
        "manifest_id",
        "candidate_universe_id",
        "blocker_name",
        "blocking_version",
        "threshold",
        "seed",
        "quotas",
        "uncertainty_margin",
        "high_score",
        "low_score",
        "max_per_name",
        "max_per_source",
        "label_instructions_version",
        "label_instructions",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise LearningContractError(f"sampling plan has unknown fields: {sorted(unknown)}")
    return SamplingPlan(**dict(raw))


def _load_blocking_survivor_ids(path: Path, expected_universe_id: str) -> tuple[str, ...]:
    raw = _read_json(path, "blocking survivors")
    if isinstance(raw, Mapping):
        universe_id = raw.get("candidate_universe_id")
        if universe_id != expected_universe_id:
            raise LearningContractError(
                "blocking survivor file candidate_universe_id does not match the pre-blocking gold set"
            )
        raw = raw.get("pair_ids")
    if not isinstance(raw, list) or not all(isinstance(value, str) and value for value in raw):
        raise LearningContractError("blocking survivor file must contain pair_ids as non-empty strings")
    if len(raw) != len(set(raw)):
        raise LearningContractError("blocking survivor file contains duplicate pair ids")
    return tuple(raw)


def sample_command(args: argparse.Namespace) -> int:
    plan = _load_plan(args.plan)
    manifest = sample_blocking_survivors(_load_candidates(args.candidates), plan)
    validate_sampling_manifest(manifest)
    write_immutable_json(args.out, manifest)
    selected = len(manifest["sampled_pairs"])
    print(f"wrote {args.out}: {selected} blocking-surviving review candidates")
    for row in manifest["strata"]:
        print(
            f"  {row['stratum']}: population={row['population']} "
            f"frame={row['sampling_frame_population']} selected={row['selected']}"
        )
    return 0


def report_command(args: argparse.Namespace) -> int:
    audit = load_label_dataset(args.audit)
    gold = load_label_dataset(args.blocking_gold)
    artifact = load_scorer_artifact(args.artifact)
    training = load_label_dataset(args.training) if args.training else None
    survivors = _load_blocking_survivor_ids(args.blocking_survivors, gold.candidate_universe_id)
    report = evaluate_candidate(
        audit=audit,
        blocking_gold=gold,
        blocking_surviving_pair_ids=survivors,
        artifact=artifact,
        training=training,
    )
    write_immutable_json(args.out, report)
    print(f"wrote {args.out}: {report['status']}")
    if report["status"] == "not_proven":
        for reason in report["not_proven_reasons"]:
            print(f"  not proven: {reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M4.1 entity-resolution learning evidence tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="sample a review queue from blocking-surviving candidates")
    sample.add_argument("--candidates", type=Path, required=True)
    sample.add_argument("--plan", type=Path, required=True)
    sample.add_argument("--out", type=Path, required=True)
    sample.set_defaults(handler=sample_command)

    report = sub.add_parser("report", help="evaluate one immutable scorer candidate")
    report.add_argument("--audit", type=Path, required=True)
    report.add_argument("--blocking-gold", type=Path, required=True)
    report.add_argument("--blocking-survivors", type=Path, required=True)
    report.add_argument("--artifact", type=Path, required=True)
    report.add_argument("--training", type=Path)
    report.add_argument("--out", type=Path, required=True)
    report.set_defaults(handler=report_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except LearningContractError as exc:
        # A malformed or insufficient artifact is an expected safety stop,
        # not an invitation to continue with a quietly weaker check.
        print(f"learning evidence check failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
