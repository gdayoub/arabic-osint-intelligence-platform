# ADR 0037: Make offline scorer evaluation tie-safe and endpoint-disjoint

## Status

Accepted for offline evaluation — 2026-10-12. No live scorer change or
production deployment is part of this decision.

## Context

Two small examples exposed false confidence in the M4.1 evidence checks:

1. With labels `[true, false]` and scores `[0.5, 0.5]`, reported average
   precision was 1.0. Reversing the rows changed it to 0.5. Sorting by score
   preserved row order inside a tie, so the result measured arbitrary input
   order. The deterministic baselines are particularly exposed because they
   only emit zero/one scores.
2. `SamplingCandidate.group_id` describes a whole pair, such as
   `Alice | Bob`. The audit check compared that whole string with
   `Alice | Carol` and accepted the datasets as disjoint despite the shared
   name. `grouped_split()` had the same problem. Manifests already retained
   endpoint groups, but the frozen-dataset parser discarded them.

Existing version-1 snapshots may instead contain an opaque group ID with no
endpoint metadata. A content digest protects their bytes; it does not prove
the meaning or completeness of those groups.

## Options considered

- Break score ties by row ID, positive label, or a seeded shuffle. Rejected:
  these impose a ranking the scorer never produced.
- Use trapezoidal area under the precision-recall curve. Rejected: this is
  a different metric from the existing intended average precision.
- Split `group_id` at ` | `. Rejected: the contract accepts arbitrary
  non-empty IDs and literal endpoint names may contain that delimiter.
- Treat legacy opaque IDs as sufficient evidence and check endpoints only
  where available. Rejected: the exact old A–B/A–C failure would remain
  silently accepted when endpoint fields were absent.
- Require a schema migration before loading any old snapshot. Rejected:
  historical artifacts should remain readable and their hashes unchanged.
- Retain explicit endpoints, tighten only the evidence checks, and use
  standard-library threshold grouping and graph traversal. Chosen.

## Decision

### Average precision

Sort scored rows once, then process every equal-score block together.
At each distinct score threshold, multiply the number of newly included
positives by the cumulative precision. Divide the sum by the total positive
count. This is non-interpolated average precision: recall increments weight
threshold precisions. Empty or no-positive inputs remain unmeasurable
(`None`, serialized as `null`), preserving the previous evidence policy.

The implementation uses `itertools.groupby` from Python's standard library.
It yields a one-use iterator of adjacent rows with the same score after
sorting. A plain nested loop consumes each block and updates the two counts.
No machine-learning dependency is added to runtime requirements.

### Endpoint-disjoint evidence

`LabeledPair` retains optional `left_group` and `right_group` fields at the
end of its dataclass, preserving existing positional construction. When
either field exists in JSON, both must be non-empty strings. Its
`name_groups` property returns the unique endpoint IDs only when both are
available; it never interprets the opaque `group_id` as a list of names.

The training/audit guard keeps the existing pair-ID and opaque-group checks,
then rejects any shared endpoint. Every row participates, including unresolved
labels. Missing endpoints are an evidence error. `evaluate_candidate()`
already translates that error into `not_proven` / `keep_current_weights`.

Splitting starts with opaque group IDs as graph vertices. Sharing either
endpoint connects two vertices. An iterative traversal collects complete
connected components, so A–B, B–C, and C–D cannot cross the split. Keeping
opaque IDs as vertices also preserves the prior constraint that all rows
assigned the same group stay together. Only one edge to the first group
seen for each name is necessary; there is no all-pairs edge expansion for
frequent names. Component members are sorted before seeded hash ranking,
making the result independent of row order and endpoint orientation.
Reusing an old seed does not promise the same partition as the earlier
whole-pair-group algorithm; only the corrected split is safe to reuse.

The test fraction applies to components, not row counts. At least two
disjoint components are required. Returned pair IDs and group IDs keep their
existing types and meaning. Duplicate pair IDs fail explicitly rather than
risk appearing in both output partitions.

### Compatibility boundary

JSON `schema_version` stays `1.0.0`: the endpoint fields already exist in
sampling manifests and the freezer already preserves supplied row fields.
The parser now retains their meaning. Group-only snapshots still load,
without rewriting content or changing a digest. Their stronger evaluation
or splitting check now fails closed instead of claiming safety from absent
information. No delimiter-based upgrade is attempted.

Recover missing endpoints from the original sampling evidence by pair ID,
then write a new immutable snapshot with a new ID/path. Any new candidate
artifact must reference the applicable new snapshot IDs/digests. Do not
overwrite old datasets or scorer artifacts. Old reports with tied scores
should be regenerated to new report paths before performance comparisons.

## Consequences and limits

- Report keys, production weights, inference scoring, deployment policy,
  database schema, and dashboard contracts are unchanged.
- The fix can lower an old AP result or make a formerly accepted split
  impossible. That corrects evidence quality; it is not a model regression
  or proof of better production resolution.
- Existing group-only data remains useful for inspection, but cannot
  establish held-out name-group safety until endpoint evidence is supplied.
- Component sizes can be uneven. A giant connected component is a sampling
  problem to expose, not a reason to weaken the split guard.
- The guarantee is relative to the supplied normalized-name/entity IDs. It
  does not discover unknown aliases or verify human labels and manifest
  membership. Those remain separate evidence requirements.
- Focused regressions cover tied-score permutations, deterministic binary
  baselines, legacy load/hash compatibility, malformed endpoints, literal
  delimiters, overlapping endpoints in both orientations, transitive
  components, and the public evaluation gate.
