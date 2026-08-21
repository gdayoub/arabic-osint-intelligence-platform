"""The evidence contract around the learned entity-resolution scorer.

The existing scorer is intentionally small: it turns a fixed feature vector
into a probability with a learned logistic-regression weight file.  That is
useful at inference time, but it is not enough to make a credible learning
claim.  A model can look good on labels it selected for itself, or on rows
whose near-duplicate names leak into both train and test.

This module keeps the learning loop deliberately file based until there are
enough real review decisions to justify a database schema change.  It gives
us four durable things now:

* a deterministic, diversity-capped sampling manifest for pairs that really
  survived blocking;
* immutable label snapshots for training, audit, and pre-blocking recall;
* group-safe evaluation and explicit evidence gates; and
* a strict metadata envelope for a *future* scorer artifact.

It does not modify ``config/pair_scorer_weights.json`` and it does not make
the current scorer deployable.  In particular, the historical golden labels
are not silently upgraded into production-shaped evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from src.resolve.blocking import Blocker
from src.resolve.features import MentionContext, compute_features
from src.resolve.scorer import PairScorer


SCHEMA_VERSION = "1.0.0"
SAMPLING_STRATA = ("uncertain", "high_score", "low_score", "diverse_name")
DatasetPurpose = Literal["training", "audit_holdout", "blocking_recall_gold"]

# This text is copied into every sampling manifest.  The separate Markdown
# guide is easier for a human to read; embedding it here makes an old,
# content-addressed manifest self-explanatory even if the guide later evolves.
LABEL_INSTRUCTIONS_V1 = (
    "Label the two mentions as same only when the source evidence identifies "
    "the same real-world entity. Treat shared family names, titles, and "
    "similar spellings as insufficient. Use different when the evidence "
    "supports distinct entities. Use unresolved rather than guessing when the "
    "available evidence cannot decide. Do not use the model score as evidence."
)


class LearningContractError(ValueError):
    """A learning artifact is malformed, mutable, or semantically unsafe."""


def _canonical_json(value: Any) -> bytes:
    """Return one stable JSON encoding suitable for a content hash.

    ``sort_keys`` makes dictionary order irrelevant.  Compact separators keep
    the digest independent of pretty-printing, and ``allow_nan=False`` keeps
    invalid floating-point values from being hidden in JSON.
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_sha256(value: Mapping[str, Any], *, hash_field: str = "content_sha256") -> str:
    """Hash a mapping while intentionally excluding its self-hash field."""

    unsigned = {key: item for key, item in value.items() if key != hash_field}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def feature_schema_sha256(feature_names: Sequence[str]) -> str:
    """Hash ordered feature names; their order is part of the model meaning."""

    return hashlib.sha256(_canonical_json(list(feature_names))).hexdigest()


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearningContractError(f"{field} must be a non-empty string")
    return value


def _require_sha256(value: Any, field: str) -> str:
    text = _require_string(value, field)
    if (
        len(text) != 64
        or text != text.lower()
        or any(char not in "0123456789abcdef" for char in text)
    ):
        raise LearningContractError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _require_probability(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise LearningContractError(f"{field} must be a finite number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise LearningContractError(f"{field} must be between 0 and 1")
    return result


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise LearningContractError(f"{field} must be a boolean")
    return value


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready immutable payload with a deterministic digest."""

    sealed = dict(value)
    sealed.pop("content_sha256", None)
    sealed["content_sha256"] = content_sha256(sealed)
    return sealed


def write_immutable_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    """Write a sealed artifact once, refusing to replace different content.

    A repeat invocation with byte-equivalent semantic content is harmless.
    Replacing a frozen audit set or scorer artifact with new content under the
    same path is not harmless, so this fails loudly instead.
    """

    sealed = _seal(value)
    encoded = json.dumps(sealed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LearningContractError(f"cannot read existing immutable artifact {path}: {exc}") from exc
        if content_sha256(existing) != existing.get("content_sha256"):
            raise LearningContractError(f"existing immutable artifact {path} has an invalid digest")
        if content_sha256(existing) != sealed["content_sha256"]:
            raise LearningContractError(
                f"refusing to replace immutable artifact {path}; choose a new dataset or artifact id"
            )
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    return sealed


@dataclass(frozen=True, slots=True)
class SamplingCandidate:
    """One pair that survived the actual blocking step.

    The normalized name groups and source names are metadata, not browser
    display values.  Keeping both sides lets the sampler stop a common person
    name or a prolific source from taking over the review queue.
    """

    pair_id: str
    left_group: str
    right_group: str
    left_source: str
    right_source: str
    score: float
    feature_vector: tuple[float, ...] = ()
    baseline_exact_match: bool = False
    baseline_gazetteer_match: bool = False

    def __post_init__(self) -> None:
        for name in ("pair_id", "left_group", "right_group", "left_source", "right_source"):
            _require_string(getattr(self, name), name)
        _require_probability(self.score, "score")
        for value in self.feature_vector:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise LearningContractError("feature_vector values must be finite numbers")

    @property
    def name_groups(self) -> tuple[str, ...]:
        return tuple(sorted({self.left_group, self.right_group}))

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(sorted({self.left_source, self.right_source}))

    @property
    def group_id(self) -> str:
        return " | ".join(self.name_groups)

    @property
    def source_group(self) -> str:
        return " | ".join(self.source_names)


def candidates_from_blocker(
    contexts: Mapping[int, MentionContext],
    blocker: Blocker,
    scorer: PairScorer,
    *,
    pair_id: Callable[[int, int], str] | None = None,
    gazetteer_match: Callable[[MentionContext, MentionContext], bool] | None = None,
) -> tuple[SamplingCandidate, ...]:
    """Adapt the existing blocker/scorer output into the sampling contract.

    This deliberately mirrors the model path in ``resolve_core``: it asks the
    supplied blocker for candidates, skips incompatible object types, computes
    the ordinary ``PairFeatures``, and scores only those surviving pairs.  It
    does *not* reconstruct the resolver's exact/gazetteer collapsing.  Callers
    that have that deterministic decision available can supply it through
    ``gazetteer_match``; otherwise the field remains false rather than being
    guessed from a score.
    """

    candidate_id = pair_id or (lambda left, right: f"{min(left, right)}:{max(left, right)}")
    pairs = blocker.candidate_pairs(
        {mention_id: set(context.blocking_keys) for mention_id, context in contexts.items()}
    )
    result: list[SamplingCandidate] = []
    for left_id, right_id in sorted(pairs):
        left = contexts[left_id]
        right = contexts[right_id]
        if left.object_type != right.object_type:
            continue
        features = compute_features(left, right)
        result.append(
            SamplingCandidate(
                pair_id=candidate_id(left_id, right_id),
                left_group=left.normalized_name,
                right_group=right.normalized_name,
                left_source=left.source,
                right_source=right.source,
                score=scorer.probability(features),
                feature_vector=tuple(features.as_vector()),
                baseline_exact_match=left.normalized_name == right.normalized_name,
                baseline_gazetteer_match=(
                    gazetteer_match(left, right) if gazetteer_match is not None else False
                ),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    """The pre-registered sampling recipe for one candidate universe.

    A random-looking but deterministic SHA-256 rank makes repeated runs with
    the same universe and seed reproduce the same queue exactly.  The
    diversity caps apply across the whole manifest, in the published stratum
    priority order. The recorded rate is tied to the capacity available before
    that stratum starts, rather than pretending the caps do not affect
    inclusion.
    """

    manifest_id: str
    candidate_universe_id: str
    blocker_name: str
    blocking_version: str
    threshold: float
    seed: int
    quotas: Mapping[str, int]
    uncertainty_margin: float = 0.10
    high_score: float = 0.80
    low_score: float = 0.20
    max_per_name: int = 2
    max_per_source: int = 3
    label_instructions_version: str = "resolution-review-v1"
    label_instructions: str = LABEL_INSTRUCTIONS_V1

    def __post_init__(self) -> None:
        for name in (
            "manifest_id",
            "candidate_universe_id",
            "blocker_name",
            "blocking_version",
            "label_instructions_version",
            "label_instructions",
        ):
            _require_string(getattr(self, name), name)
        _require_probability(self.threshold, "threshold")
        _require_probability(self.uncertainty_margin, "uncertainty_margin")
        _require_probability(self.high_score, "high_score")
        _require_probability(self.low_score, "low_score")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise LearningContractError("seed must be an integer")
        if self.max_per_name < 1 or self.max_per_source < 1:
            raise LearningContractError("diversity caps must be at least one")
        if self.low_score > self.threshold - self.uncertainty_margin:
            raise LearningContractError("low_score must sit below the uncertainty band")
        if self.high_score < self.threshold + self.uncertainty_margin:
            raise LearningContractError("high_score must sit above the uncertainty band")
        unknown = set(self.quotas) - set(SAMPLING_STRATA)
        if unknown:
            raise LearningContractError(f"unknown sampling strata: {sorted(unknown)}")
        for stratum in SAMPLING_STRATA:
            value = self.quotas.get(stratum, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise LearningContractError(f"quota for {stratum} must be a non-negative integer")


def _stratum(candidate: SamplingCandidate, plan: SamplingPlan) -> tuple[str, str]:
    """Put every candidate in one disjoint, explainable review bucket."""

    if abs(candidate.score - plan.threshold) <= plan.uncertainty_margin:
        return "uncertain", "score_within_uncertainty_margin"
    if candidate.score >= plan.high_score:
        return "high_score", "score_at_or_above_high_score_cutoff"
    if candidate.score <= plan.low_score:
        return "low_score", "score_at_or_below_low_score_cutoff"
    return "diverse_name", "mid_score_coverage_after_score_strata"


def _rank_candidate(candidate: SamplingCandidate, plan: SamplingPlan, stratum: str) -> str:
    # The separator prevents ambiguity such as (1, 23) versus (12, 3).
    raw = f"{plan.seed}\x1f{plan.candidate_universe_id}\x1f{stratum}\x1f{candidate.pair_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _candidate_payload(candidate: SamplingCandidate) -> dict[str, Any]:
    return {
        "pair_id": candidate.pair_id,
        "left_group": candidate.left_group,
        "right_group": candidate.right_group,
        "left_source": candidate.left_source,
        "right_source": candidate.right_source,
        "group_id": candidate.group_id,
        "source_group": candidate.source_group,
        "score": candidate.score,
        "feature_vector": list(candidate.feature_vector),
        "baseline_exact_match": candidate.baseline_exact_match,
        "baseline_gazetteer_match": candidate.baseline_gazetteer_match,
    }


def sample_blocking_survivors(
    candidates: Iterable[SamplingCandidate], plan: SamplingPlan
) -> dict[str, Any]:
    """Create a sealed sampling manifest for blocking-surviving candidates.

    The algorithm is intentionally boring:

    1. assign a disjoint score stratum;
    2. shuffle deterministically with a hash of the supplied seed;
    3. fill each stratum in a fixed priority order while applying one set of
       diversity counts to the *whole* manifest; and
    4. record any quota a global cap made impossible to fill.

    The selection rate stored on every selected row is ``selected /
    pre-stratum-frame-population``. It is explicitly labelled as a realized,
    deterministic sampling rate because global caps make it unsuitable for
    pretending there was an unrestricted simple random sample.
    """

    normalized = tuple(candidates)
    ids = [candidate.pair_id for candidate in normalized]
    if len(ids) != len(set(ids)):
        raise LearningContractError("candidate pair_id values must be unique")

    by_stratum: dict[str, list[tuple[SamplingCandidate, str]]] = {
        stratum: [] for stratum in SAMPLING_STRATA
    }
    for candidate in normalized:
        stratum, reason = _stratum(candidate, plan)
        by_stratum[stratum].append((candidate, reason))

    strata_summary: list[dict[str, Any]] = []
    sampled_pairs: list[dict[str, Any]] = []
    # These counters intentionally live outside the stratum loop. A name or
    # source selected in the uncertainty band consumes the same diversity
    # budget in the high-, low-, and mid-score bands.
    name_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for stratum in SAMPLING_STRATA:
        entries = by_stratum[stratum]
        ranked = sorted(
            entries,
            key=lambda item: (_rank_candidate(item[0], plan, stratum), item[0].pair_id),
        )
        # The frame captures the capacity remaining before this stratum made
        # any selections. It does not charge later strata for candidates that
        # were merely considered and never entered the review queue.
        frame = [
            item
            for item in ranked
            if not any(name_counts[name] >= plan.max_per_name for name in item[0].name_groups)
            and not any(
                source_counts[source] >= plan.max_per_source for source in item[0].source_names
            )
        ]
        requested = plan.quotas.get(stratum, 0)
        selected: list[tuple[SamplingCandidate, str, str]] = []
        excluded_by_name = 0
        excluded_by_source = 0
        for candidate, reason in ranked:
            if len(selected) >= requested:
                break
            if any(name_counts[name] >= plan.max_per_name for name in candidate.name_groups):
                excluded_by_name += 1
                continue
            if any(source_counts[source] >= plan.max_per_source for source in candidate.source_names):
                excluded_by_source += 1
                continue
            rank = _rank_candidate(candidate, plan, stratum)
            selected.append((candidate, reason, rank))
            name_counts.update(candidate.name_groups)
            source_counts.update(candidate.source_names)

        probability = len(selected) / len(frame) if frame else 0.0
        quota_shortfall = max(0, requested - len(selected))
        quota_shortfall_due_to_global_caps = max(
            0, min(requested, len(entries)) - len(selected)
        )
        if quota_shortfall_due_to_global_caps:
            shortfall_reason: str | None = "global_diversity_caps"
        elif quota_shortfall:
            shortfall_reason = "stratum_population_below_quota"
        else:
            shortfall_reason = None
        strata_summary.append(
            {
                "stratum": stratum,
                "population": len(entries),
                "sampling_frame_population": len(frame),
                "requested": requested,
                "selected": len(selected),
                "conditional_sampling_probability": probability,
                "excluded_by_name_cap": excluded_by_name,
                "excluded_by_source_cap": excluded_by_source,
                "quota_shortfall": quota_shortfall,
                "quota_shortfall_due_to_global_caps": quota_shortfall_due_to_global_caps,
                "quota_shortfall_reason": shortfall_reason,
            }
        )
        for candidate, reason, rank in selected:
            payload = _candidate_payload(candidate)
            payload["sampling"] = {
                "stratum": stratum,
                "reason": reason,
                "sampling_probability": probability,
                "sampling_probability_basis": (
                    "realized deterministic selection rate from the pre-stratum "
                    "global-cap-eligible frame"
                ),
                "stratum_population": len(entries),
                "sampling_frame_population": len(frame),
                "stratum_requested": requested,
                "stratum_selected": len(selected),
                "global_diversity_cap_scope": "full_manifest",
                "deterministic_rank_sha256": rank,
            }
            sampled_pairs.append(payload)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "blocking_survivor_sampling_manifest",
        "manifest_id": plan.manifest_id,
        "candidate_universe": {
            "id": plan.candidate_universe_id,
            "stage": "blocking_surviving",
            "blocker_name": plan.blocker_name,
            "blocking_version": plan.blocking_version,
            "population": len(normalized),
        },
        "plan": {
            "seed": plan.seed,
            "threshold": plan.threshold,
            "uncertainty_margin": plan.uncertainty_margin,
            "high_score": plan.high_score,
            "low_score": plan.low_score,
            "quotas": {stratum: plan.quotas.get(stratum, 0) for stratum in SAMPLING_STRATA},
            "max_per_name": plan.max_per_name,
            "max_per_source": plan.max_per_source,
            "diversity_cap_scope": "full_manifest",
            "stratum_priority": list(SAMPLING_STRATA),
            "label_instructions_version": plan.label_instructions_version,
            "label_instructions": plan.label_instructions,
        },
        "strata": strata_summary,
        "sampled_pairs": sampled_pairs,
    }
    return _seal(payload)


def validate_sampling_manifest(value: Mapping[str, Any]) -> None:
    """Check both the manifest hash and the information needed to reproduce it."""

    if value.get("schema_version") != SCHEMA_VERSION:
        raise LearningContractError("unsupported sampling manifest schema_version")
    if value.get("kind") != "blocking_survivor_sampling_manifest":
        raise LearningContractError("not a blocking-survivor sampling manifest")
    _require_string(value.get("manifest_id"), "manifest_id")
    if content_sha256(value) != value.get("content_sha256"):
        raise LearningContractError("sampling manifest content_sha256 does not match its content")
    universe = value.get("candidate_universe")
    if not isinstance(universe, Mapping) or universe.get("stage") != "blocking_surviving":
        raise LearningContractError("sampling manifest must describe a blocking-surviving universe")
    _require_string(universe.get("id"), "candidate_universe.id")
    _require_string(universe.get("blocker_name"), "candidate_universe.blocker_name")
    _require_string(universe.get("blocking_version"), "candidate_universe.blocking_version")
    plan = value.get("plan")
    if not isinstance(plan, Mapping):
        raise LearningContractError("sampling manifest needs a plan")
    if plan.get("diversity_cap_scope") != "full_manifest":
        raise LearningContractError("sampling manifest diversity caps must cover the full manifest")
    if plan.get("stratum_priority") != list(SAMPLING_STRATA):
        raise LearningContractError("sampling manifest must use the declared stratum priority")
    max_per_name = plan.get("max_per_name")
    max_per_source = plan.get("max_per_source")
    if not isinstance(max_per_name, int) or isinstance(max_per_name, bool) or max_per_name < 1:
        raise LearningContractError("sampling manifest max_per_name must be a positive integer")
    if not isinstance(max_per_source, int) or isinstance(max_per_source, bool) or max_per_source < 1:
        raise LearningContractError("sampling manifest max_per_source must be a positive integer")
    raw_strata = value.get("strata")
    if not isinstance(raw_strata, list):
        raise LearningContractError("sampling manifest strata must be a list")
    seen_strata: set[str] = set()
    for summary in raw_strata:
        if not isinstance(summary, Mapping) or summary.get("stratum") not in SAMPLING_STRATA:
            raise LearningContractError("sampling manifest has an invalid stratum summary")
        stratum = summary["stratum"]
        if stratum in seen_strata:
            raise LearningContractError("sampling manifest repeats a stratum summary")
        seen_strata.add(stratum)
        for field in (
            "population",
            "sampling_frame_population",
            "requested",
            "selected",
            "quota_shortfall",
            "quota_shortfall_due_to_global_caps",
        ):
            item = summary.get(field)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise LearningContractError(f"sampling manifest {field} must be a non-negative integer")
        if summary["quota_shortfall"] != summary["requested"] - summary["selected"]:
            raise LearningContractError("sampling manifest quota_shortfall does not match requested minus selected")
        if summary["quota_shortfall_due_to_global_caps"] > summary["quota_shortfall"]:
            raise LearningContractError("global-cap shortfall cannot exceed the total quota shortfall")
    if not isinstance(value.get("sampled_pairs"), list):
        raise LearningContractError("sampling manifest sampled_pairs must be a list")
    seen: set[str] = set()
    selected_name_counts: Counter[str] = Counter()
    selected_source_counts: Counter[str] = Counter()
    selected_by_stratum: Counter[str] = Counter()
    for pair in value["sampled_pairs"]:
        if not isinstance(pair, Mapping):
            raise LearningContractError("sampling manifest pair must be an object")
        pair_id = _require_string(pair.get("pair_id"), "sampled_pairs.pair_id")
        if pair_id in seen:
            raise LearningContractError("sampling manifest contains a duplicate pair_id")
        seen.add(pair_id)
        sampling = pair.get("sampling")
        if not isinstance(sampling, Mapping):
            raise LearningContractError("sampling manifest pair must include sampling metadata")
        if sampling.get("stratum") not in SAMPLING_STRATA:
            raise LearningContractError("sampling manifest pair has an unknown stratum")
        selected_by_stratum[sampling["stratum"]] += 1
        _require_probability(sampling.get("sampling_probability"), "sampling_probability")
        _require_string(sampling.get("reason"), "sampling.reason")
        if sampling.get("global_diversity_cap_scope") != "full_manifest":
            raise LearningContractError("sampling manifest pair has the wrong diversity-cap scope")
        left_group = _require_string(pair.get("left_group"), "sampled_pairs.left_group")
        right_group = _require_string(pair.get("right_group"), "sampled_pairs.right_group")
        left_source = _require_string(pair.get("left_source"), "sampled_pairs.left_source")
        right_source = _require_string(pair.get("right_source"), "sampled_pairs.right_source")
        selected_name_counts.update({left_group, right_group})
        selected_source_counts.update({left_source, right_source})
    if any(count > max_per_name for count in selected_name_counts.values()):
        raise LearningContractError("sampling manifest exceeds max_per_name across the full manifest")
    if any(count > max_per_source for count in selected_source_counts.values()):
        raise LearningContractError("sampling manifest exceeds max_per_source across the full manifest")
    for summary in raw_strata:
        if summary["selected"] != selected_by_stratum[summary["stratum"]]:
            raise LearningContractError("sampling manifest summary selected count does not match its pairs")


@dataclass(frozen=True, slots=True)
class LabeledPair:
    """A labeled pair in a frozen data snapshot.

    ``label`` may be ``None`` while a sampled queue is awaiting review.  Such
    rows are kept visible in reports but never count as evidence.
    """

    pair_id: str
    group_id: str
    source_group: str
    label: bool | None
    feature_vector: tuple[float, ...]
    baseline_exact_match: bool
    baseline_gazetteer_match: bool
    sampling: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FrozenLabelDataset:
    dataset_id: str
    purpose: DatasetPurpose
    candidate_universe_id: str
    candidate_universe_stage: str
    blocking_version: str | None
    feature_names: tuple[str, ...]
    pairs: tuple[LabeledPair, ...]
    sampling_manifest_id: str | None
    sampling_manifest_sha256: str | None
    content_digest: str

    @property
    def labeled_pairs(self) -> tuple[LabeledPair, ...]:
        return tuple(pair for pair in self.pairs if pair.label is not None)

    @property
    def positive_count(self) -> int:
        return sum(pair.label is True for pair in self.labeled_pairs)

    @property
    def negative_count(self) -> int:
        return sum(pair.label is False for pair in self.labeled_pairs)


def _parse_feature_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(name, str) and name for name in value):
        raise LearningContractError("feature_names must be a non-empty list of strings")
    names = tuple(value)
    if len(names) != len(set(names)):
        raise LearningContractError("feature_names must not contain duplicates")
    return names


def _parse_labeled_pair(
    raw: Mapping[str, Any], feature_count: int, purpose: DatasetPurpose
) -> LabeledPair:
    pair_id = _require_string(raw.get("pair_id"), "pairs.pair_id")
    group_id = _require_string(raw.get("group_id"), "pairs.group_id")
    source_group = _require_string(raw.get("source_group", "unknown"), "pairs.source_group")
    label = raw.get("label")
    if label is not None and not isinstance(label, bool):
        raise LearningContractError("pairs.label must be true, false, or null")
    vector_raw = raw.get("feature_vector", [])
    if purpose != "blocking_recall_gold" and (
        not isinstance(vector_raw, list) or len(vector_raw) != feature_count
    ):
        raise LearningContractError("audit and training pairs need one value for each feature")
    if purpose == "blocking_recall_gold" and vector_raw not in ([], None):
        raise LearningContractError("pre-blocking gold pairs must not carry scorer features")
    vector: list[float] = []
    for value in vector_raw or []:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise LearningContractError("feature_vector values must be finite numbers")
        vector.append(float(value))
    sampling = raw.get("sampling")
    if purpose in ("training", "audit_holdout"):
        if not isinstance(sampling, Mapping):
            raise LearningContractError("blocking-surviving pair needs sampling metadata")
        if sampling.get("stratum") not in SAMPLING_STRATA:
            raise LearningContractError("pair sampling metadata has an unknown stratum")
        _require_probability(sampling.get("sampling_probability"), "pairs.sampling.sampling_probability")
        _require_string(sampling.get("reason"), "pairs.sampling.reason")
    return LabeledPair(
        pair_id=pair_id,
        group_id=group_id,
        source_group=source_group,
        label=label,
        feature_vector=tuple(vector),
        baseline_exact_match=_require_bool(
            raw.get("baseline_exact_match", False), "pairs.baseline_exact_match"
        ),
        baseline_gazetteer_match=_require_bool(
            raw.get("baseline_gazetteer_match", False), "pairs.baseline_gazetteer_match"
        ),
        sampling=dict(sampling) if isinstance(sampling, Mapping) else None,
    )


def validate_label_dataset(value: Mapping[str, Any]) -> FrozenLabelDataset:
    """Validate an immutable training, audit, or blocking-recall snapshot."""

    if value.get("schema_version") != SCHEMA_VERSION:
        raise LearningContractError("unsupported label dataset schema_version")
    if value.get("kind") != "resolution_label_dataset":
        raise LearningContractError("not a resolution label dataset")
    if value.get("frozen") is not True:
        raise LearningContractError("label dataset must be explicitly frozen")
    if content_sha256(value) != value.get("content_sha256"):
        raise LearningContractError("label dataset content_sha256 does not match its content")
    dataset_id = _require_string(value.get("dataset_id"), "dataset_id")
    purpose = value.get("purpose")
    if purpose not in ("training", "audit_holdout", "blocking_recall_gold"):
        raise LearningContractError("unknown label dataset purpose")
    universe = value.get("candidate_universe")
    if not isinstance(universe, Mapping):
        raise LearningContractError("label dataset needs candidate_universe metadata")
    universe_id = _require_string(universe.get("id"), "candidate_universe.id")
    stage = universe.get("stage")
    expected_stage = "pre_blocking" if purpose == "blocking_recall_gold" else "blocking_surviving"
    if stage != expected_stage:
        raise LearningContractError(
            f"{purpose} must use a {expected_stage!r} candidate universe, not {stage!r}"
        )
    feature_names = _parse_feature_names(value.get("feature_names"))
    if purpose == "blocking_recall_gold" and feature_names != ("pair_id_only",):
        raise LearningContractError("pre-blocking gold must use feature_names ['pair_id_only']")
    raw_pairs = value.get("pairs")
    if not isinstance(raw_pairs, list):
        raise LearningContractError("label dataset pairs must be a list")
    parsed = tuple(_parse_labeled_pair(raw, len(feature_names), purpose) for raw in raw_pairs if isinstance(raw, Mapping))
    if len(parsed) != len(raw_pairs):
        raise LearningContractError("label dataset pair must be an object")
    ids = [pair.pair_id for pair in parsed]
    if len(ids) != len(set(ids)):
        raise LearningContractError("label dataset contains duplicate pair_id values")
    if purpose in ("training", "audit_holdout"):
        _require_string(value.get("sampling_manifest_id"), "sampling_manifest_id")
        _require_sha256(value.get("sampling_manifest_sha256"), "sampling_manifest_sha256")
        blocking_version = _require_string(universe.get("blocking_version"), "candidate_universe.blocking_version")
    else:
        raw_blocking_version = universe.get("blocking_version")
        blocking_version = (
            _require_string(raw_blocking_version, "candidate_universe.blocking_version")
            if raw_blocking_version is not None
            else None
        )
    return FrozenLabelDataset(
        dataset_id=dataset_id,
        purpose=purpose,
        candidate_universe_id=universe_id,
        candidate_universe_stage=stage,
        blocking_version=blocking_version,
        feature_names=feature_names,
        pairs=parsed,
        sampling_manifest_id=value.get("sampling_manifest_id"),
        sampling_manifest_sha256=value.get("sampling_manifest_sha256"),
        content_digest=value["content_sha256"],
    )


def freeze_label_dataset(
    *,
    dataset_id: str,
    purpose: DatasetPurpose,
    candidate_universe_id: str,
    pairs: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    sampling_manifest_id: str | None = None,
    sampling_manifest_sha256: str | None = None,
    blocking_version: str | None = None,
    label_instructions_version: str = "resolution-review-v1",
) -> dict[str, Any]:
    """Create a sealed label snapshot after reviewers have supplied labels."""

    expected_stage = "pre_blocking" if purpose == "blocking_recall_gold" else "blocking_surviving"
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "resolution_label_dataset",
        "dataset_id": dataset_id,
        "purpose": purpose,
        "frozen": True,
        "candidate_universe": {"id": candidate_universe_id, "stage": expected_stage},
        "feature_names": list(feature_names),
        "label_instructions_version": label_instructions_version,
        "pairs": [dict(pair) for pair in pairs],
    }
    if blocking_version is not None:
        payload["candidate_universe"]["blocking_version"] = blocking_version
    if purpose != "blocking_recall_gold":
        payload["sampling_manifest_id"] = sampling_manifest_id
        payload["sampling_manifest_sha256"] = sampling_manifest_sha256
    sealed = _seal(payload)
    validate_label_dataset(sealed)
    return sealed


def load_label_dataset(path: Path) -> FrozenLabelDataset:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningContractError(f"cannot read label dataset {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise LearningContractError("label dataset root must be an object")
    return validate_label_dataset(raw)


def assert_training_audit_disjoint(
    training: FrozenLabelDataset, audit: FrozenLabelDataset
) -> None:
    """Reject both direct pair overlap and group leakage into the audit set."""

    if training.purpose != "training":
        raise LearningContractError("first dataset must be a training snapshot")
    if audit.purpose != "audit_holdout":
        raise LearningContractError("second dataset must be an audit holdout")
    overlapping_pairs = {pair.pair_id for pair in training.pairs} & {pair.pair_id for pair in audit.pairs}
    if overlapping_pairs:
        raise LearningContractError(
            f"training and audit snapshots share pair ids: {sorted(overlapping_pairs)[:5]}"
        )
    training_groups = {pair.group_id for pair in training.pairs}
    audit_groups = {pair.group_id for pair in audit.pairs}
    overlapping_groups = training_groups & audit_groups
    if overlapping_groups:
        raise LearningContractError(
            "training and audit snapshots share normalized-name/entity groups: "
            f"{sorted(overlapping_groups)[:5]}"
        )


@dataclass(frozen=True, slots=True)
class GroupedSplit:
    train_pair_ids: tuple[str, ...]
    test_pair_ids: tuple[str, ...]
    train_groups: tuple[str, ...]
    test_groups: tuple[str, ...]
    seed: int


def grouped_split(
    pairs: Sequence[LabeledPair], *, test_fraction: float, seed: int
) -> GroupedSplit:
    """Split by normalized-name/entity group instead of by individual rows."""

    if not 0.0 < test_fraction < 1.0:
        raise LearningContractError("test_fraction must be strictly between 0 and 1")
    by_group: dict[str, list[LabeledPair]] = defaultdict(list)
    for pair in pairs:
        by_group[pair.group_id].append(pair)
    if len(by_group) < 2:
        raise LearningContractError("grouped split needs at least two distinct groups")
    ranked_groups = sorted(
        by_group,
        key=lambda group: (hashlib.sha256(f"{seed}\x1f{group}".encode("utf-8")).hexdigest(), group),
    )
    test_groups_count = round(len(ranked_groups) * test_fraction)
    test_groups_count = min(max(test_groups_count, 1), len(ranked_groups) - 1)
    test_groups = tuple(sorted(ranked_groups[:test_groups_count]))
    train_groups = tuple(sorted(ranked_groups[test_groups_count:]))
    test_group_set = set(test_groups)
    test_ids = tuple(sorted(pair.pair_id for pair in pairs if pair.group_id in test_group_set))
    train_ids = tuple(sorted(pair.pair_id for pair in pairs if pair.group_id not in test_group_set))
    return GroupedSplit(
        train_pair_ids=train_ids,
        test_pair_ids=test_ids,
        train_groups=train_groups,
        test_groups=test_groups,
        seed=seed,
    )


@dataclass(frozen=True, slots=True)
class BlockingRecall:
    positive_gold_pairs: int
    surviving_positive_pairs: int
    recall: float | None
    target: float
    support_sufficient: bool


def blocking_recall(
    gold: FrozenLabelDataset,
    blocking_surviving_pair_ids: Iterable[str],
    *,
    target: float = 0.95,
    min_positive_support: int = 60,
) -> BlockingRecall:
    """Measure the recall ceiling on pre-blocking gold positives only."""

    if gold.purpose != "blocking_recall_gold":
        raise LearningContractError("blocking recall requires a pre-blocking gold dataset")
    _require_probability(target, "target")
    if min_positive_support < 1:
        raise LearningContractError("min_positive_support must be at least one")
    survivors = set(blocking_surviving_pair_ids)
    positives = {pair.pair_id for pair in gold.labeled_pairs if pair.label is True}
    survived = len(positives & survivors)
    recall = survived / len(positives) if positives else None
    return BlockingRecall(
        positive_gold_pairs=len(positives),
        surviving_positive_pairs=survived,
        recall=recall,
        target=target,
        support_sufficient=len(positives) >= min_positive_support,
    )


@dataclass(frozen=True, slots=True)
class ScorerArtifact:
    """A future immutable scorer weight artifact, not the current legacy file."""

    artifact_id: str
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    threshold: float
    training_dataset_id: str
    training_label_snapshot_sha256: str
    blocking_version: str
    code_commit: str
    random_seed: int
    evaluation_set_id: str
    metrics: Mapping[str, Any]
    content_digest: str

    def probability(self, feature_vector: Sequence[float]) -> float:
        if len(feature_vector) != len(self.coefficients):
            raise LearningContractError("feature vector does not match scorer artifact feature count")
        total = self.intercept
        for coefficient, value in zip(self.coefficients, feature_vector):
            total += coefficient * value
        if total >= 0:
            return 1.0 / (1.0 + math.exp(-total))
        exp_total = math.exp(total)
        return exp_total / (1.0 + exp_total)


def validate_scorer_artifact(value: Mapping[str, Any]) -> ScorerArtifact:
    """Validate all metadata needed to reproduce or roll back a candidate."""

    if value.get("schema_version") != SCHEMA_VERSION:
        raise LearningContractError("unsupported scorer artifact schema_version")
    if value.get("kind") != "resolution_scorer_artifact":
        raise LearningContractError("not an immutable resolution scorer artifact")
    if content_sha256(value) != value.get("content_sha256"):
        raise LearningContractError("scorer artifact content_sha256 does not match its content")
    artifact_id = _require_string(value.get("artifact_id"), "artifact_id")
    feature_names = _parse_feature_names(value.get("feature_names"))
    if _require_sha256(value.get("feature_schema_sha256"), "feature_schema_sha256") != feature_schema_sha256(feature_names):
        raise LearningContractError("feature_schema_sha256 does not match ordered feature_names")
    raw_coefficients = value.get("coefficients")
    if not isinstance(raw_coefficients, list) or len(raw_coefficients) != len(feature_names):
        raise LearningContractError("coefficients must match feature_names in length")
    coefficients: list[float] = []
    for coefficient in raw_coefficients:
        if not isinstance(coefficient, (int, float)) or isinstance(coefficient, bool) or not math.isfinite(coefficient):
            raise LearningContractError("coefficients must be finite numbers")
        coefficients.append(float(coefficient))
    intercept_raw = value.get("intercept")
    if not isinstance(intercept_raw, (int, float)) or isinstance(intercept_raw, bool) or not math.isfinite(intercept_raw):
        raise LearningContractError("intercept must be a finite number")
    threshold = _require_probability(value.get("threshold"), "threshold")
    _require_string(value.get("created_at"), "created_at")
    training_id = _require_string(value.get("training_dataset_id"), "training_dataset_id")
    snapshot_hash = _require_sha256(
        value.get("training_label_snapshot_sha256"), "training_label_snapshot_sha256"
    )
    blocking_version = _require_string(value.get("blocking_version"), "blocking_version")
    code_commit = _require_string(value.get("code_commit"), "code_commit")
    if len(code_commit) < 7 or len(code_commit) > 64 or any(
        char not in "0123456789abcdef" for char in code_commit.lower()
    ):
        raise LearningContractError("code_commit must be a Git hexadecimal revision")
    seed = value.get("random_seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise LearningContractError("random_seed must be an integer")
    evaluation_set_id = _require_string(value.get("evaluation_set_id"), "evaluation_set_id")
    metrics = value.get("metrics")
    if not isinstance(metrics, Mapping):
        raise LearningContractError("metrics must be an object")
    for metric_name in ("precision", "recall", "pr_auc"):
        _require_probability(metrics.get(metric_name), f"metrics.{metric_name}")
    calibration = metrics.get("calibration")
    if not isinstance(calibration, list):
        raise LearningContractError("metrics.calibration must be a list")
    model_merges = metrics.get("model_originated_merges")
    if not isinstance(model_merges, int) or isinstance(model_merges, bool) or model_merges < 0:
        raise LearningContractError("metrics.model_originated_merges must be a non-negative integer")
    return ScorerArtifact(
        artifact_id=artifact_id,
        feature_names=feature_names,
        coefficients=tuple(coefficients),
        intercept=float(intercept_raw),
        threshold=threshold,
        training_dataset_id=training_id,
        training_label_snapshot_sha256=snapshot_hash,
        blocking_version=blocking_version,
        code_commit=code_commit,
        random_seed=seed,
        evaluation_set_id=evaluation_set_id,
        metrics=dict(metrics),
        content_digest=value["content_sha256"],
    )


def freeze_scorer_artifact(
    *,
    artifact_id: str,
    feature_names: Sequence[str],
    coefficients: Sequence[float],
    intercept: float,
    threshold: float,
    created_at: str,
    training_dataset_id: str,
    training_label_snapshot_sha256: str,
    blocking_version: str,
    code_commit: str,
    random_seed: int,
    evaluation_set_id: str,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal a future candidate scorer without changing the live scorer path."""

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "resolution_scorer_artifact",
        "artifact_id": artifact_id,
        "feature_names": list(feature_names),
        "feature_schema_sha256": feature_schema_sha256(feature_names),
        "coefficients": list(coefficients),
        "intercept": intercept,
        "threshold": threshold,
        "created_at": created_at,
        "training_dataset_id": training_dataset_id,
        "training_label_snapshot_sha256": training_label_snapshot_sha256,
        "blocking_version": blocking_version,
        "code_commit": code_commit,
        "random_seed": random_seed,
        "evaluation_set_id": evaluation_set_id,
        "metrics": dict(metrics),
    }
    sealed = _seal(payload)
    validate_scorer_artifact(sealed)
    return sealed


def load_scorer_artifact(path: Path) -> ScorerArtifact:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearningContractError(f"cannot read scorer artifact {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise LearningContractError("scorer artifact root must be an object")
    return validate_scorer_artifact(raw)


@dataclass(frozen=True, slots=True)
class BinaryMetrics:
    support: int
    positives: int
    negatives: int
    predicted_positive: int
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float | None
    recall: float | None
    f1: float | None
    pr_auc: float | None
    brier_score: float | None
    calibration: tuple[dict[str, float | int], ...]


def _binary_metrics(labels: Sequence[bool], scores: Sequence[float], predictions: Sequence[bool]) -> BinaryMetrics:
    if not (len(labels) == len(scores) == len(predictions)):
        raise LearningContractError("labels, scores, and predictions need equal lengths")
    tp = sum(label and predicted for label, predicted in zip(labels, predictions))
    fp = sum(not label and predicted for label, predicted in zip(labels, predictions))
    fn = sum(label and not predicted for label, predicted in zip(labels, predictions))
    tn = sum(not label and not predicted for label, predicted in zip(labels, predictions))
    positives = tp + fn
    negatives = fp + tn
    predicted_positive = tp + fp
    precision = tp / predicted_positive if predicted_positive else None
    recall = tp / positives if positives else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    pr_auc = _average_precision(labels, scores)
    brier = sum((score - float(label)) ** 2 for label, score in zip(labels, scores)) / len(labels) if labels else None
    return BinaryMetrics(
        support=len(labels),
        positives=positives,
        negatives=negatives,
        predicted_positive=predicted_positive,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        pr_auc=pr_auc,
        brier_score=brier,
        calibration=_calibration(labels, scores),
    )


def _average_precision(labels: Sequence[bool], scores: Sequence[float]) -> float | None:
    positives = sum(labels)
    if not positives:
        return None
    ranked = sorted(zip(scores, labels), key=lambda item: item[0], reverse=True)
    true_so_far = 0
    total = 0.0
    for index, (_score, label) in enumerate(ranked, start=1):
        if label:
            true_so_far += 1
            total += true_so_far / index
    return total / positives


def _calibration(labels: Sequence[bool], scores: Sequence[float], bins: int = 5) -> tuple[dict[str, float | int], ...]:
    if not labels:
        return ()
    grouped: list[list[tuple[bool, float]]] = [[] for _ in range(bins)]
    for label, score in zip(labels, scores):
        index = min(int(score * bins), bins - 1)
        grouped[index].append((label, score))
    result: list[dict[str, float | int]] = []
    for index, values in enumerate(grouped):
        if not values:
            continue
        result.append(
            {
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": len(values),
                "mean_prediction": sum(score for _label, score in values) / len(values),
                "observed_positive_rate": sum(label for label, _score in values) / len(values),
            }
        )
    return tuple(result)


def _metric_value(metrics: BinaryMetrics, name: str) -> float | None:
    if name == "f1":
        return metrics.f1
    if name == "precision":
        return metrics.precision
    if name == "recall":
        return metrics.recall
    raise LearningContractError(f"unsupported paired metric {name}")


@dataclass(frozen=True, slots=True)
class PairedInterval:
    metric: str
    point_estimate: float | None
    lower: float | None
    upper: float | None
    bootstrap_samples_used: int


def paired_bootstrap_interval(
    labels: Sequence[bool],
    candidate_predictions: Sequence[bool],
    baseline_predictions: Sequence[bool],
    *,
    metric: Literal["f1", "precision", "recall"] = "f1",
    seed: int,
    samples: int = 2000,
) -> PairedInterval:
    """A paired percentile interval: both systems see the same sampled rows."""

    if not (len(labels) == len(candidate_predictions) == len(baseline_predictions)):
        raise LearningContractError("paired interval inputs need equal lengths")
    if samples < 1:
        raise LearningContractError("bootstrap samples must be at least one")
    if not labels:
        return PairedInterval(metric, None, None, None, 0)
    candidate = _metric_value(_binary_metrics(labels, [float(p) for p in candidate_predictions], candidate_predictions), metric)
    baseline = _metric_value(_binary_metrics(labels, [float(p) for p in baseline_predictions], baseline_predictions), metric)
    point = candidate - baseline if candidate is not None and baseline is not None else None
    rng = random.Random(seed)
    deltas: list[float] = []
    indexes = range(len(labels))
    for _ in range(samples):
        drawn = [rng.randrange(len(labels)) for _ in indexes]
        drawn_labels = [labels[index] for index in drawn]
        candidate_metrics = _binary_metrics(
            drawn_labels,
            [float(candidate_predictions[index]) for index in drawn],
            [candidate_predictions[index] for index in drawn],
        )
        baseline_metrics = _binary_metrics(
            drawn_labels,
            [float(baseline_predictions[index]) for index in drawn],
            [baseline_predictions[index] for index in drawn],
        )
        candidate_value = _metric_value(candidate_metrics, metric)
        baseline_value = _metric_value(baseline_metrics, metric)
        if candidate_value is not None and baseline_value is not None:
            deltas.append(candidate_value - baseline_value)
    if not deltas:
        return PairedInterval(metric, point, None, None, 0)
    deltas.sort()
    return PairedInterval(
        metric=metric,
        point_estimate=point,
        lower=_percentile(deltas, 0.025),
        upper=_percentile(deltas, 0.975),
        bootstrap_samples_used=len(deltas),
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise LearningContractError("cannot calculate percentile of no values")
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def wilson_lower_bound(successes: int, trials: int, *, z: float = 1.645) -> float | None:
    """One-sided Wilson lower confidence bound for an observed precision."""

    if trials < 0 or successes < 0 or successes > trials:
        raise LearningContractError("Wilson successes must be between zero and trials")
    if not trials:
        return None
    proportion = successes / trials
    denominator = 1 + z * z / trials
    centre = proportion + z * z / (2 * trials)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * trials)) / trials)
    return (centre - margin) / denominator


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    """Pre-registered evidence thresholds, deliberately stricter than 51 labels."""

    min_audit_positives: int = 60
    min_audit_negatives: int = 60
    min_predicted_positive: int = 60
    min_blocking_gold_positives: int = 60
    precision_floor: float = 0.95
    blocking_recall_target: float = 0.95
    bootstrap_samples: int = 2000
    bootstrap_seed: int = 20260821

    def __post_init__(self) -> None:
        for name in (
            "min_audit_positives",
            "min_audit_negatives",
            "min_predicted_positive",
            "min_blocking_gold_positives",
            "bootstrap_samples",
        ):
            if getattr(self, name) < 1:
                raise LearningContractError(f"{name} must be at least one")
        _require_probability(self.precision_floor, "precision_floor")
        _require_probability(self.blocking_recall_target, "blocking_recall_target")


def _metrics_payload(metrics: BinaryMetrics) -> dict[str, Any]:
    return {
        "support": metrics.support,
        "positives": metrics.positives,
        "negatives": metrics.negatives,
        "predicted_positive": metrics.predicted_positive,
        "true_positive": metrics.true_positive,
        "false_positive": metrics.false_positive,
        "false_negative": metrics.false_negative,
        "true_negative": metrics.true_negative,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
        "pr_auc": metrics.pr_auc,
        "brier_score": metrics.brier_score,
        "calibration": list(metrics.calibration),
    }


def _interval_payload(interval: PairedInterval) -> dict[str, Any]:
    return {
        "metric": interval.metric,
        "point_estimate": interval.point_estimate,
        "lower": interval.lower,
        "upper": interval.upper,
        "bootstrap_samples_used": interval.bootstrap_samples_used,
    }


def evaluate_candidate(
    *,
    audit: FrozenLabelDataset,
    blocking_gold: FrozenLabelDataset,
    blocking_surviving_pair_ids: Iterable[str],
    artifact: ScorerArtifact,
    training: FrozenLabelDataset | None,
    policy: EvaluationPolicy | None = None,
) -> dict[str, Any]:
    """Produce a conservative deployment report for a future scorer artifact.

    The only positive status is ``eligible_for_manual_deployment_review``. It
    is still not an automatic deployment: this module intentionally has no
    write path into the live scorer configuration.
    """

    policy = policy or EvaluationPolicy()
    if audit.purpose != "audit_holdout":
        raise LearningContractError("audit must be an audit_holdout dataset")
    if blocking_gold.purpose != "blocking_recall_gold":
        raise LearningContractError("blocking_gold must be a blocking_recall_gold dataset")
    if artifact.evaluation_set_id != audit.dataset_id:
        raise LearningContractError("artifact evaluation_set_id does not name the supplied audit dataset")
    if artifact.feature_names != audit.feature_names:
        raise LearningContractError("artifact feature_names do not match the frozen audit dataset")
    if artifact.blocking_version != audit.blocking_version:
        raise LearningContractError("artifact blocking_version does not match the frozen audit universe")

    reasons: list[str] = []
    if training is None:
        reasons.append("training snapshot was not supplied, so audit disjointness is not proven")
    else:
        if artifact.training_dataset_id != training.dataset_id:
            reasons.append("artifact training_dataset_id does not match the supplied training snapshot")
        if artifact.training_label_snapshot_sha256 != training.content_digest:
            reasons.append("artifact training_label_snapshot_sha256 does not match the supplied training snapshot")
        try:
            assert_training_audit_disjoint(training, audit)
        except LearningContractError as exc:
            reasons.append(str(exc))

    labeled = audit.labeled_pairs
    if len(labeled) != len(audit.pairs):
        reasons.append("audit holdout still contains unlabeled pairs")
    labels = [bool(pair.label) for pair in labeled]
    scores = [artifact.probability(pair.feature_vector) for pair in labeled]
    candidate_predictions = [score >= artifact.threshold for score in scores]
    exact_predictions = [pair.baseline_exact_match for pair in labeled]
    gazetteer_predictions = [pair.baseline_gazetteer_match for pair in labeled]
    candidate_metrics = _binary_metrics(labels, scores, candidate_predictions)
    exact_metrics = _binary_metrics(labels, [float(value) for value in exact_predictions], exact_predictions)
    gazetteer_metrics = _binary_metrics(
        labels, [float(value) for value in gazetteer_predictions], gazetteer_predictions
    )
    exact_lift = paired_bootstrap_interval(
        labels,
        candidate_predictions,
        exact_predictions,
        seed=policy.bootstrap_seed,
        samples=policy.bootstrap_samples,
    )
    gazetteer_lift = paired_bootstrap_interval(
        labels,
        candidate_predictions,
        gazetteer_predictions,
        seed=policy.bootstrap_seed + 1,
        samples=policy.bootstrap_samples,
    )
    recall = blocking_recall(
        blocking_gold,
        blocking_surviving_pair_ids,
        target=policy.blocking_recall_target,
        min_positive_support=policy.min_blocking_gold_positives,
    )

    if candidate_metrics.positives < policy.min_audit_positives:
        reasons.append(
            f"audit has {candidate_metrics.positives} positive labels; need {policy.min_audit_positives}"
        )
    if candidate_metrics.negatives < policy.min_audit_negatives:
        reasons.append(
            f"audit has {candidate_metrics.negatives} negative labels; need {policy.min_audit_negatives}"
        )
    if candidate_metrics.predicted_positive < policy.min_predicted_positive:
        reasons.append(
            "candidate has "
            f"{candidate_metrics.predicted_positive} predicted matches; need "
            f"{policy.min_predicted_positive} for a precision bound"
        )
    precision_lower = wilson_lower_bound(
        candidate_metrics.true_positive, candidate_metrics.predicted_positive
    )
    if precision_lower is None or precision_lower < policy.precision_floor:
        reasons.append(
            "one-sided Wilson precision lower bound does not clear the "
            f"{policy.precision_floor:.2f} deployment floor"
        )
    if not recall.support_sufficient:
        reasons.append(
            f"blocking gold has {recall.positive_gold_pairs} positive pairs; "
            f"need {policy.min_blocking_gold_positives}"
        )
    if recall.recall is None or recall.recall < policy.blocking_recall_target:
        reasons.append(
            "blocking recall does not clear the "
            f"{policy.blocking_recall_target:.2f} target"
        )
    for baseline_name, interval in (("exact_match", exact_lift), ("gazetteer", gazetteer_lift)):
        if interval.lower is None or interval.lower <= 0.0:
            reasons.append(
                f"paired F1 lift over {baseline_name} is not proven above zero"
            )

    status = "eligible_for_manual_deployment_review" if not reasons else "not_proven"
    model_only = sum(
        predicted and not exact and not gazetteer
        for predicted, exact, gazetteer in zip(
            candidate_predictions, exact_predictions, gazetteer_predictions
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "resolution_scorer_evaluation_report",
        "status": status,
        "deployment_action": (
            "manual_review_required" if status != "not_proven" else "keep_current_weights"
        ),
        "not_proven_reasons": reasons,
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "content_sha256": artifact.content_digest,
            "training_dataset_id": artifact.training_dataset_id,
            "evaluation_set_id": artifact.evaluation_set_id,
            "blocking_version": artifact.blocking_version,
            "code_commit": artifact.code_commit,
            "random_seed": artifact.random_seed,
        },
        "audit": {
            "dataset_id": audit.dataset_id,
            "content_sha256": audit.content_digest,
            "candidate_universe_id": audit.candidate_universe_id,
            "sampling_manifest_id": audit.sampling_manifest_id,
            "sampling_manifest_sha256": audit.sampling_manifest_sha256,
            "labeled_pairs": len(labeled),
            "unlabeled_pairs": len(audit.pairs) - len(labeled),
            "sampling_design_included": audit.sampling_manifest_id is not None,
        },
        "support_policy": {
            "min_audit_positives": policy.min_audit_positives,
            "min_audit_negatives": policy.min_audit_negatives,
            "min_predicted_positive": policy.min_predicted_positive,
            "min_blocking_gold_positives": policy.min_blocking_gold_positives,
            "precision_floor": policy.precision_floor,
            "blocking_recall_target": policy.blocking_recall_target,
            "paired_interval": "paired percentile bootstrap, 95% two-sided",
            "precision_interval": "one-sided 95% Wilson lower bound",
        },
        "metrics": {
            "candidate": _metrics_payload(candidate_metrics),
            "exact_match_baseline": _metrics_payload(exact_metrics),
            "gazetteer_baseline": _metrics_payload(gazetteer_metrics),
            "paired_f1_lift_vs_exact_match": _interval_payload(exact_lift),
            "paired_f1_lift_vs_gazetteer": _interval_payload(gazetteer_lift),
            "model_only_predicted_matches": model_only,
            "baseline_predicted_matches": {
                "exact_match": exact_metrics.predicted_positive,
                "gazetteer": gazetteer_metrics.predicted_positive,
            },
        },
        "blocking_recall": {
            "gold_dataset_id": blocking_gold.dataset_id,
            "positive_gold_pairs": recall.positive_gold_pairs,
            "surviving_positive_pairs": recall.surviving_positive_pairs,
            "recall": recall.recall,
            "target": recall.target,
            "support_sufficient": recall.support_sufficient,
        },
    }
