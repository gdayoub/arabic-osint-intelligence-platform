# ADR 0025: Anchor human constraints to durable evidence before stabilizing entities

## Status

Accepted — 2026-08-21

## Context

The M4 resolver intentionally rebuilds its `EntityORM` clusters on every
run.  It retracts the previous generation and creates new integer entity rows.
That is honest about the current implementation, but it means a raw mention
ID is also not a durable endpoint for a human must-link or cannot-link: a
version bump creates replacement mention rows, and a later resolver could no
longer find the decision without guessing from text.

M4.2 eventually needs stable real-world entity UIDs, append-only
continued/merged/split lineage, and as-of membership.  Those public identity
semantics deserve their own checkpoint.  This first slice establishes the
lower-level evidence identity that every later entity, graph, bilingual search,
and review feature can safely depend on without changing resolver output or
the current dashboard contract.

## Decision

### Durable document and evidence identities

`0004_evidence_identity` adds four tables without altering existing document,
mention, entity, decision, or public snapshot fields:

- `document_identities` maps every `documents.id` to one immutable,
  never-reused UUID `document_uid`.
- `evidence_identities` stores one durable fingerprint for an exact original
  source span.
- `mention_evidence_identities` maps every versioned raw `mentions.id` to its
  evidence identity.
- `resolution_constraints` mirrors each human `resolution_decisions` row with
  durable evidence endpoints and explicit supersession.

New documents receive UUID4 values inside the same sanctioned write
transaction as their `documents` row.  Pre-M4.2 rows receive deterministic
UUID5 values derived from their existing document ID only during the explicit
adoption operation, so interruption/retry cannot create a different UID.  A
document UID distinguishes two publishers carrying byte-for-byte identical
wire copy; those sources must never share a human-constraint endpoint merely
because their text matches.

An evidence fingerprint is a canonical SHA-256 over:

1. the durable document UID;
2. the SHA-256 of the original source text;
3. Python Unicode code-point start and end offsets;
4. the configured object type; and
5. a declared BCP-47 language tag.

The fingerprint version is recorded.  Extractor name/version are deliberately
not fingerprint inputs: they remain on raw mention and provenance rows, so an
exact span emitted by a new extractor version remaps to the same evidence.
Every new extractor must declare a language through the extractor protocol;
`create_mention` requires it explicitly and rejects `und`.  `und` remains
available only as an explicit operator choice while adopting legacy rows; it
is never a silent production default.

The identity tables contain hashes, offsets, type, language, and IDs only.
They do not copy a mention string, document body, sentence, or snippet.  Raw
text stays behind the existing blob/provenance boundary.  Future M5 evidence
artifacts must separately define and test their bounded sentence disclosure
boundary.  Because the fingerprint binds source SHA and original offsets,
document text is immutable for identity purposes: a corrected or
paragraph-preserving representation must be a new document version/UID, not
an in-place text mutation.

### Constraints and read-only remapping

`record_decision` still writes the existing append-only raw decision exactly
as before, then creates its parallel durable `resolution_constraints` row in
the same transaction.  It records two `ProvenanceORM` entries using the
original raw mention IDs, so `main.py provenance show evidence_identities ID`
and `main.py provenance show resolution_constraints ID` remain inspectable.

An explicit raw decision supersession maps to an explicit durable constraint
supersession.  If a declared predecessor has not been adopted into a durable
constraint, the write fails rather than quietly dropping the lineage.  The
adoption command processes decisions in append order, so it establishes those
predecessors safely.

`remap_resolution_constraints` is deliberately a read-only projection in
this checkpoint:

- an active constraint with live raw mentions for both exact fingerprints is
  `remapped`;
- either absent live fingerprint is `unresolved`;
- opposing active decisions for the same durable pair, or a cannot-link from
  an evidence fingerprint to itself, is `conflict`.

No text similarity, shifted-offset heuristic, recency rule, or entity ID is
allowed to resolve an `unresolved` state.  Independent conflicting facts stay
visible rather than allowing the newest row to silently overwrite history.
The current resolver continues to read its existing raw decisions and returns
the same entities/statistics; it does not consume this projection yet.

### Deferred to M4.2b

This ADR intentionally does not assign a stable real-world entity UID,
rewrite `EntityORM.id`, add a generation table, change the public API, or
alter the dashboard.  The next checkpoint must define a separate stable
entity table and append-only lineage/as-of rules: unchanged evidence
continues a UID; merges retain a deterministic predecessor and emit aliases;
the largest deterministic split child continues the old UID; other children
receive new UIDs; retractions remain historically queryable.  Only then may
the resolver consume remapped constraints and publish stable entity identity.

## Alternatives considered

1. **Keep constraints on raw mention IDs.** Rejected because an extractor
   replacement necessarily loses their endpoints.
2. **Fingerprint normalized text only.** Rejected because it would collapse
   syndicated documents and could remap a shifted span to a different source
   occurrence.
3. **Include extractor version in the fingerprint.** Rejected because it
   defeats exact version-bump continuity; extractor version remains P4
   provenance instead.
4. **Fuzzy-remap shifted spans.** Rejected because an incorrect must-link or
   cannot-link is worse than a visible review item.
5. **Build stable entities and resolver-generation applications now.**
   Rejected because that changes public identity/output behavior before its
   merge/split/as-of semantics have been approved.

## Rollout and recovery

1. Create a recovery reference, then apply `0003_publication_state` to
   `0004_evidence_identity` through the existing confirmed schema workflow.
   The migration is expand-only; empty-to-head and `0003 -> 0004` are tested.
2. Deploy the dual-write code.  New document/mention/decision writes now
   create identities in their existing caller transaction.
3. Before enabling new human decisions over legacy mentions, run a read-only
   audit, supplying real extractor languages:

   ```sh
   python scripts/adopt_m42_identity.py --check \
     --extractor-language gazetteer_extractor=ar \
     --database-url "$DATABASE_URL"
   ```

4. Resolve all reported source/hash/offset/language problems.  Run the same
   command with `--apply`; it validates first, writes one transaction, emits
   a JSON report, and is safe to resume after interruption.  `--default-language`
   is allowed only when an operator consciously owns that legacy assumption.
5. Inspect `remap_status_counts`; unresolved/conflict rows are review work,
   not candidates for automatic application.  Do not change resolver output
   until M4.2b accepts stable entity/generation semantics.

There is no destructive Alembic downgrade.  If the expanded feature needs to
be stopped, disable calls to the read-only remapper and retain the additive
tables plus raw decisions.  A bad DDL deployment uses the recorded database
recovery reference; a data problem receives a forward repair or a restored
backup, never deletion of evidence/constraint history.

## Consequences

- Human decisions now have exact, inspectable continuity across re-extraction
  when source text and offsets are unchanged.
- Shifted spans and contradictory decisions become explicit states that a
  later review/generation workflow can surface.
- The current UI, baked contract, entity IDs, and resolver behavior remain
  unchanged.
- Every future stable entity/graph/search design can use evidence
  fingerprints rather than transient M4 entity or mention generation IDs.
