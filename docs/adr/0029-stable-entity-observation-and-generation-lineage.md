# ADR 0029: Observe stable entity identity beside disposable resolver clusters

## Status

Accepted — 2026-08-21

## Context

The current M4 resolver intentionally treats `EntityORM` as a disposable
generation: every full recompute retracts the old rows and writes new integer
IDs.  That is safe for its current dashboard aggregate, but it is not a
durable identity for an entity URL, a future watchlist, graph node, or
bilingual profile.  Reusing an old integer entity ID would also be dishonest:
there is no guarantee that the next cluster with that number represents the
same real-world thing.

ADR 0025 established the lower-level continuity key first.  Every new mention
now maps to a durable evidence identity whose fingerprint binds the document
UID, source-text hash, original offsets, configured object type, and language.
Human must-link/cannot-link constraints are anchored to those evidence IDs,
but the legacy resolver still reads raw mention decisions and has not yet been
approved to enforce the durable remap projection.

The risk in adding stable IDs directly to `entities` is twofold:

1. It would turn a known-disposable table into a mixed historical/current
   contract, making rollback and provenance harder to explain.
2. It would silently change resolver output while constraint conflict,
   migration, and public-identity semantics are still being tested.

## Options considered

1. **Add a `stable_uid` column to `entities`.** Rejected.  A recompute creates
   new entity rows, so a column there either changes every run or requires
   unsafe in-place reuse of a disposable row.
2. **Choose a stable UID from normalized text or canonical name.** Rejected.
   Names change, aliases collide, and normalization deliberately does not
   identify a real-world entity.  It would violate the evidence-first rule.
3. **Make M4 consume durable constraints and publish stable IDs immediately.**
   Rejected.  M4.2a intentionally marks missing/shifted spans and conflicts
   visible rather than guessing.  Enforcing them before an explicit resolver
   checkpoint would change production clustering without an accepted conflict
   policy.
4. **Build an explicit observe-only projection beside `EntityORM` — chosen.**
   It creates durable identity/history from the existing output without
   changing that output, its API, its dashboard data, or release behavior.

## Decision

`0005_stable_entity_generations` adds an append-only stable-identity
projection:

- `stable_entities` holds a never-reused UUID and configured object type.
  It has no mutable current/retracted flag.
- `resolver_generations` records one explicitly observed resolver input/output
  with the resolver extractor version, reconciliation algorithm version,
  durable input digest, parent generation, and durable-constraint status
  counts.
- `stable_entity_snapshots` records *every known stable entity* in every
  generation.  A present snapshot names the disposable source `EntityORM`,
  canonical name, and membership digest.  An absent snapshot deliberately has
  no source entity or membership.  This full snapshot rule makes "as of"
  membership/canonical-name queries truthful instead of accidentally returning
  the most recent older evidence as if it were current.
- `stable_entity_memberships` reference M4.2a `evidence_identities`, with one
  deterministic raw mention anchor only for provenance.  Evidence, not a
  disposable mention ID, is the membership continuity key.  The membership
  records its snapshot generation through a composite foreign key and makes
  `(generation_id, evidence_identity_id)` unique, so one durable evidence
  endpoint cannot be current for two stable entities in one generation.
- `stable_entity_lineage` appends `continued`, `merged_into`, and `split_into`
  edges.  It replaces the unused `EntityORM.supersedes_id` concept for durable
  entity history; `EntityORM.supersedes_id` remains untouched legacy storage.
- `stable_entity_lineage_evidence` attaches every lineage edge to the exact
  overlapping durable evidence identities and the current membership carrying
  each source-span provenance anchor.  A composite foreign key prevents its
  membership anchor from naming a different evidence endpoint; history reads
  additionally fail closed unless that membership is in the edge target's
  snapshot for the edge generation.  An edge is therefore inspectable as
  evidence, not only as a reconciliation label.

These historical tables reject ORM updates/deletes and receive PostgreSQL
append-only triggers.  The only mutable row is
`stable_entity_resolution_state`.  It is a singleton coordination pointer,
not evidence: a transaction locks it, creates a complete immutable
generation, then points it at that generation last.  The observer explicitly
acquires that shared lock before flushing caller-pending resolver output and
writes inside a savepoint, so a
late failure cannot leave a caught, inactive partial generation behind.  A
shared transaction-scoped PostgreSQL advisory lock also serializes it with
the legacy resolver's entity rewrite.  A failure rolls back the new rows and
leaves the preceding observed generation active.

### Continuity rule

Reconciliation compares exact durable evidence sets only.  It does not use
normalized text, fuzzy matching, canonical-name similarity, or a raw mention
ID.

- With one predecessor and one overlapping current cluster, that predecessor
  is `continued`.
- When multiple prior entities overlap one current cluster, it is a merge.
  The predecessor with the greatest exact-evidence overlap survives.  A tie is
  broken by lexical stable UUID, so different process ordering cannot change
  the answer.  Other predecessors append `merged_into` edges to it.
- When one predecessor overlaps multiple current clusters, its largest exact
  overlap child survives.  Ties use only the configured object type and
  sorted durable evidence fingerprints; mutable canonical names are never a
  continuity input.  Other children receive new stable UUIDs and append
  `split_into` edges from the predecessor.
- A cluster with no active predecessor gets a new UUID.  A prior entity with
  no current overlap receives an absent snapshot, preserving history without
  inventing a redirect.

Continuity is deliberately adjacent-active-generation only.  An entity that
was absent and later reappears receives a new UID rather than resurrecting an
old merged/retracted identity.  This conservative choice prevents a stale
alias from silently reclaiming a current entity; a later explicit review rule
can add a forward lineage repair if evidence warrants it.

### Observe-only gate and constraints

`observe_live_entity_generation()` is an explicit backend call.  The current
`resolve_all()` path does not call it (it only shares the output lock), and no
public contract,
`data.json`, UI, or release state changes in this checkpoint.  Passing any
mode other than `observe` fails.  The observer records counts from ADR 0025's
read-only constraint remap (`remapped`, `unresolved`, `conflict`) in its
immutable generation record, but it does not apply a must-link/cannot-link or
alter a legacy cluster.  That is the safe visibility bridge for the later
resolver-enforcement checkpoint.

Observation fails closed when a live `EntityORM` references a missing or
retracted mention/document, lacks one provenance row per source membership,
or has mixed/mismatched resolver extractor provenance.  The operator must
run the legacy resolver again rather than let stale evidence become current
stable membership.  Conversely, when `resolve_all()` finds zero live contexts
it retracts the prior disposable entity generation under the same lock, so a
subsequent explicit observation can truthfully write all-absent snapshots.

`scripts/show_stable_entity_history.py` is a separate read-only CLI while the
feature remains observational.  It verifies schema head and prints the exact
active or requested generation's snapshot, memberships, direct lineage with
its durable evidence/source-span witnesses, and safe alias targets.  It does
not appear in `main.py`, invoke the resolver, or write a state pointer.

## Rollout and recovery

1. Create a recovery reference and run the existing confirmed schema workflow
   from `0004_evidence_identity` to `0005_stable_entity_generations`.
2. Deploy code.  Nothing automatically observes a generation, so the legacy
   resolver and public snapshot remain unchanged.
3. Exercise the observer against a verified, non-production or explicitly
   approved production transaction.  Inspect the generation's constraint
   status counts and history output before proposing resolver enforcement.
4. If observation must stop, stop invoking the explicit observer.  Existing
   immutable records stay as honest historical facts; no destructive downgrade
   is permitted.  A DDL/data failure uses the prepared recovery reference or a
   forward repair, never deletion of lineage.

## Consequences

- A stable entity UUID can survive repeated unchanged full recomputes even
  though every source `EntityORM.id` changes.
- Merge and split choices are deterministic, evidence-backed, and explainable
  through immutable lineage plus membership provenance.
- An old merged stable URL can resolve to its active successor; a split's
  retained primary child keeps the old URL, while new children receive their
  own IDs.
- The project gains a safe as-of/history contract for M5/M6 without claiming
  that stable identity or durable constraints are live resolver behavior yet.
- Full snapshot rows grow with generations × known stable entities.  The
  current corpus is small, and correctness of historical absence is worth that
  bounded cost.  Measure before introducing a range-compression optimization.
