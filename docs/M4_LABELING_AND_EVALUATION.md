# M4.1 labeling and evaluation evidence

This is the safety rail around the entity-resolution scorer. It does not
retrain or activate the live scorer. The current
[`config/pair_scorer_weights.json`](../config/pair_scorer_weights.json) has
51 historical golden labels and is intentionally **not** a M4.1 deployment
artifact.

The goal is simple: collect real labels from pairs that could actually reach
the scorer, keep evaluation separate from training, and say “not proven”
when the evidence is too small.

## What to label

One row is a pair of mentions that survived the resolver's blocking step.
The sampler splits them into four disjoint reasons:

- `uncertain` — close to the current threshold;
- `high_score` — likely automatic matches worth checking for overconfidence;
- `low_score` — likely non-matches worth checking for blind spots; and
- `diverse_name` — mid-score coverage after the first three buckets.

For every pair:

1. Mark `same` only when the source evidence identifies the same real-world
   entity.
2. Mark `different` when the evidence supports different entities.
3. Leave it unresolved rather than guessing when the available evidence is
   insufficient.
4. Do not use the model score as evidence. Similar spelling, shared family
   name, or a title alone is not enough to merge people.

The immutable sampling manifest embeds this instruction text and its version
so a future report always says which instructions were used.

## Why there are two evaluation sets

An audit set sampled after blocking cannot tell us how many true matches the
blocker discarded. We need both:

| Dataset purpose | Population | Allowed use |
|---|---|---|
| `training` | blocking-surviving reviewed pairs | Fit a candidate scorer |
| `audit_holdout` | independently sampled blocking-surviving pairs | Score a candidate once; never train on it |
| `blocking_recall_gold` | pre-blocking labeled pairs or labeled entity clusters | Estimate blocking recall only |

Training and audit snapshots are rejected if they share a pair ID **or** a
normalized name/entity group. Grouped splits use the group as the unit, so a
frequent name cannot leak a near-duplicate into both sides.

## Create a deterministic review sample

Make an internal candidate file from an actual resolver run. The
`candidates_from_blocker()` adapter in `src.resolve.learning` takes the
existing `MentionContext`, `KeyBlocker`, and `PairScorer` output so it cannot
silently include a pair that did not survive blocking. The file contains no
stored document text; use stable mention-pair IDs and comparison metadata.

```json
{
  "candidates": [
    {
      "pair_id": "mention:17|mention:42",
      "left_group": "بشار الاسد",
      "right_group": "بشار الاسد",
      "left_source": "BBCArabic",
      "right_source": "AlJazeeraArabic",
      "score": 0.63,
      "feature_vector": [0.98, 1.0, 0.2, 0.9, 0.0, 1.0],
      "baseline_exact_match": true,
      "baseline_gazetteer_match": false
    }
  ]
}
```

The plan is checked rather than inferred. Its IDs should change for a new
candidate universe; never reuse a manifest ID for different contents.

```json
{
  "manifest_id": "review-sample-2026-08-21",
  "candidate_universe_id": "mentions-after-run-2026-08-21",
  "blocker_name": "key_blocker",
  "blocking_version": "key-blocker-v1",
  "threshold": 0.6,
  "seed": 20260821,
  "quotas": {
    "uncertain": 30,
    "high_score": 20,
    "low_score": 20,
    "diverse_name": 30
  },
  "max_per_name": 2,
  "max_per_source": 3
}
```

Run:

```bash
python scripts/evaluate_pair_scorer.py sample \
  --candidates work/blocking-survivors.json \
  --plan work/review-sampling-plan.json \
  --out work/review-sampling-manifest.json
```

One name/source cap applies across the **entire** manifest in the published
stratum order, not once per bucket. The output records each stratum's raw
population, pre-stratum globally eligible frame population, requested and
selected counts, and any quota shortfall caused by the global cap. Each
selected pair carries the realized deterministic selection rate from that
frame; it does not pretend that the caps had no effect.

## Freeze labels and evaluate a candidate

After review, create separate frozen JSON files for the training snapshot,
audit holdout, and pre-blocking gold set. The Python helpers in
`src.resolve.learning` seal them with `content_sha256`; use
`write_immutable_json()` to refuse accidental replacement. A future training
step must create a **new** scorer artifact with:

- coefficients, intercept, threshold, and ordered feature-schema hash;
- training dataset ID and exact training snapshot digest;
- blocking version, source-code commit, and random seed;
- frozen audit set ID; and
- precision, recall, PR-AUC, calibration, and model-originated merge metadata.

Then run:

```bash
python scripts/evaluate_pair_scorer.py report \
  --training data/labels/training-v1.json \
  --audit data/labels/audit-v1.json \
  --blocking-gold data/labels/blocking-gold-v1.json \
  --blocking-survivors work/blocking-survivor-ids.json \
  --artifact artifacts/pair-scorer-v2.json \
  --out reports/pair-scorer-v2-evaluation.json
```

`--blocking-survivors` is a checked record from the same pre-blocking
candidate universe:

```json
{
  "candidate_universe_id": "mentions-after-run-2026-08-21",
  "pair_ids": ["mention:17|mention:42"]
}
```

The report includes candidate, exact-match, and gazetteer metrics; PR-AUC and
calibration; model-only predicted matches; pre-blocking blocking recall; and
paired 95% bootstrap F1 intervals against both deterministic baselines.

## Gate, not a victory lap

The default policy is pre-registered in `EvaluationPolicy`:

- at least 60 positive and 60 negative audit labels;
- at least 60 candidate predicted matches for the one-sided 95% Wilson
  precision bound;
- a 0.95 precision lower bound and 0.95 blocking-recall target;
- at least 60 positive pre-blocking gold pairs; and
- paired F1 lift intervals above zero against both exact and gazetteer
  baselines.

Anything less produces `not_proven` and `keep_current_weights`. That outcome
is expected for the current label set and is the correct result, not a failed
experiment.

## Deliberately deferred database work

`review_pairs` currently lacks immutable sampling metadata. Before this file
format is wired into the live queue, propose an additive migration for the
candidate universe ID, blocker version, sampling-manifest digest, stratum,
reason, inclusion probability, and diversity groups. Keep it separate from
the first production label collection so the data contract can be reviewed
without silently changing the deployed resolver.
