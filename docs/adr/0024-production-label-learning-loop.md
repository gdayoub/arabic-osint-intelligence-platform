# ADR 0024: Freeze production-shaped resolution evidence before retraining

## Status

Accepted — 2026-08-21

## Context

M4 has a learned pair scorer, an append-only decision table, and a review
queue. It does not yet have enough evidence to claim that the learned scorer
improves production resolution. The shipped scorer has 51 historical golden
labels, and many of its negative pairs do not survive inference blocking.
That makes them useful regression examples, but not an evaluation population
for a model that only ever sees blocking-surviving pairs.

The immediate risk is a flattering but false report: sample only the
near-threshold pairs a model selected, train and test on near-duplicates of
the same names, and call a small metric change a deployment win. A second
risk is treating an after-blocking holdout as a blocking-recall measurement;
it cannot contain the true pairs that blocking discarded.

The current `review_pairs` schema does not record sampling-stratum,
candidate-universe, inclusion-probability, or frozen-holdout metadata. Adding
those fields needs a normal expand/migrate/contract decision and production
rollout, not a stealth ORM `create_all()` change.

## Options considered

1. **Train from the current golden file and report leave-one-out AUC.**
   Rejected. It still has unsupported negative examples and does not measure
   the candidate population at inference.
2. **Use only the existing near-threshold review queue.** Rejected. It would
   let the model decide its own evaluation distribution and would over-sample
   the most frequent names and sources.
3. **Add database columns and wire the live queue immediately.** Deferred.
   The required schema migration and production rollout deserve their own
   review after the evidence format is exercised on real decisions.
4. **Build an immutable, file-based learning contract first — chosen.** It
   records deterministic selection design, supports a separately frozen audit
   set and pre-blocking gold set, and proves the gates in tests without
   asserting that test labels are production evidence.

## Decision

`src/resolve/learning.py` is the M4.1 evidence contract. It creates a sealed
sampling manifest from *blocking-surviving* candidates. Sampling uses four
disjoint score strata: uncertain, high score, low score, and mid-score
diverse-name coverage. A deterministic SHA-256 rank and published seed make
the manifest reproducible. One set of diversity caps applies across the
whole manifest in a declared stratum priority order, so a frequent normalized
name or source cannot consume the queue once per bucket. Each selected row
records its stratum, reason, raw population, pre-stratum frame population,
requested/selected counts, deterministic rank, and realized sampling rate.
Each stratum also records a quota shortfall and whether a global cap caused
it, instead of silently filling the queue with repeated names or sources.

The contract has three explicit immutable dataset purposes:

- `training`: blocking-surviving, reviewed pairs used to fit a candidate;
- `audit_holdout`: an independently sampled blocking-surviving slice frozen
  before training; and
- `blocking_recall_gold`: a separate pre-blocking gold set used only to
  estimate whether real matches survived blocking.

Training and audit datasets must share neither pair IDs nor normalized
name/entity groups. Grouped evaluation splits by group rather than individual
rows. A candidate scorer is a new, content-addressed artifact containing its
weights, ordered feature-schema hash, threshold, blocking version, label
snapshot hash, code commit, seed, evaluation-set ID, and metrics metadata.
The legacy live weights file intentionally fails this stricter validator; no
code in this milestone alters it.

The deployment gate is deliberately conservative: 60 positive and 60
negative audit labels, 60 predicted matches for a precision bound, and 60
positive pre-blocking gold pairs. At 60 observed correct predictions out of
60, the one-sided 95% Wilson lower bound is approximately 0.957, so it can
clear the 0.95 precision floor. The report also requires blocking recall of
at least 0.95 and a 95% paired-bootstrap F1 lift interval above zero against
both exact-match and gazetteer baselines. If any condition is absent, it says
`not_proven` and recommends keeping the current weights.

`scripts/evaluate_pair_scorer.py` supplies reproducible `sample` and
`report` commands. It has no deploy or live-weight-writing command.

## Consequences

- The current scorer remains active but is not eligible for a production
  learning claim. Its old AUC remains historical context, not a release gate.
- The first 75–150 real decisions can now be captured against a declared
  sampling design and assessed honestly. If the learning curve says more
  labels are needed, that is a valid result.
- A follow-up schema proposal should add immutable sampling metadata to each
  review queue row (universe ID, blocker version, manifest digest, stratum,
  reason, probability, name/source groups) and a table or storage location
  for frozen label snapshots. No migration is included here.
- The file format is easy to inspect and roll back: old scorer artifacts stay
  addressable by digest, and changing an existing frozen file is refused.
