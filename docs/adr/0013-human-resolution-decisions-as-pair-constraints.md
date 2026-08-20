# ADR 0013: Human resolution decisions as append-only pair constraints

## Context

The learned pair scorer currently contributes no production merges.  Its 51
training pairs were chosen separately from inference, and 17 of 24 negatives
do not survive blocking.  M4 needs a review queue that shows the pairs the
scorer actually sees, records a person's answer with provenance, and makes
that answer affect later resolution runs.

Manual correction also has to survive the resolver's full recompute.  Editing
the current `entities` rows would appear fixed until the next six-hour run,
which retracts the current generation and builds another one.

## Options considered

1. **Update entities and queue rows in place.** Simple, but the next full
   recompute loses the correction, and an overwritten answer cannot explain
   what changed (P5).
2. **Store entity IDs in permanent merge/split rules.** Entity IDs are not
   stable: every resolution run creates a new generation and retracts the old
   one.  Rules tied to those IDs become stale immediately.
3. **Store append-only decisions between stable mention IDs — chosen.** A
   `same` decision is a must-link; a `different` decision is a cannot-link.
   The current resolver maps those mentions to today's representative groups
   and applies the constraints on every recompute.

## Decision

`review_pairs` stores immutable snapshots of blocking-surviving pairs whose
score is within 0.15 of the current threshold.  Each snapshot records the two
mention IDs, object type, score, threshold, all six feature values, and scorer
version.  Two provenance rows connect it to the exact source spans.

`resolution_decisions` stores the human answer separately.  Rows are never
updated.  A corrected answer appends another row whose `supersedes_id` points
to the previous one.  The latest row for a mention pair is the active answer.
It records the reviewer, reason, whether it came from the queue or a manual
merge/split, and the human-review extractor version.  It also carries one
provenance row per mention.

Accepted decisions become must-links even when the scorer rejected the pair
or blocking never generated it.  Rejected decisions become cannot-links:

- before blocking, they can partition an exact-spelling or gazetteer group;
- during clustering, they prevent an indirect Union-Find chain from putting
  the two mentions back together.

Manual merge selects one stable evidence mention from each live entity and
records a must-link.  Manual split names two evidence mentions in one live
entity and records a cannot-link.  The commands record the constraint; the
next `resolve-core` run applies it and creates a new entity generation.

Reviewed queue labels export as JSON with the real feature vectors observed
at inference.  `scripts/train_pair_scorer.py --review-labels FILE` can append
them to the original golden set.  Retraining remains a separate checkpoint so
a handful of early answers cannot silently replace production weights.

## Consequences

- Human answers survive scheduled full recomputation without referring to
  unstable entity IDs.
- Decisions are auditable and reversible by appending a superseding answer.
- The label set now comes from the distribution the scorer encounters.
- A manual split is pairwise, not a full named partition.  Separating a large
  ambiguous entity may require more than one cannot-link decision.  This is
  explicit work rather than an inferred partition the reviewer never chose.
- Queue insertion performs a lookup per near-threshold candidate.  The live
  resolver currently scores only a small number of distinct surface forms;
  if measurements show this becoming material, the safe optimization is one
  preload query, not a new dependency.
- No web interface or dependency was added.  The CLI is sufficient to learn
  whether the review model is useful before investing in presentation code.
