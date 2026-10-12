# ADR 0035: Bounded pipeline phase logs and empty-constraint partitioning

## Context

A scraper's last `returned N articles` message describes a network result,
not a committed source transaction. The scheduled job can spend time writing
blobs, documents and provenance after that message, then loading contexts,
partitioning, scoring, or rewriting resolver evidence. The durable ledger
records run/stage/source lifecycle facts, but its event schema deliberately
has no fine-grained resolver timings. A job killed at its deadline may never
emit a terminal event. A missing terminal alone does not identify the cause.

`_partition_on_cannot_links` previously checked every pair in a group even
when its forbidden-pair set was empty. For a group of 10,000 distinct mention
IDs that is 49,995,000 set lookups whose result is already known. Full entity
resolution still does necessary database and provenance work around that
helper; a faster helper is not proof of an end-to-end timeout fix.

## Options considered

1. Raise the job timeout, lease, or source limits again. Rejected here: those
   change operating policy without identifying the phase that needs time.
2. Add phase events to the ledger or a telemetry service. Deferred: a schema
   change or new dependency is unnecessary for diagnosing runner progress.
3. Rewrite global resolution incrementally or batch provenance writes.
   Deferred: these change correctness-sensitive behavior and need measured
   evidence plus separate design work.
4. Add bounded, content-free runner logs and remove only the provably empty
   constraint work. Chosen.

## Decision

### Runner diagnostics

- `src/ops/runtime.py` logs a start and success/failure boundary for each
  stage, with monotonic elapsed seconds and validated input/output/error
  counts. A failed operation also emits a closed `unexpected_error` notice
  before attempting to close source/ledger observations. No exception text
  or traceback is included by these log statements.
- Stage starts are logged before their durable start append, so a blocked
  ledger write does not disappear from runner diagnostics. A success log
  follows the stage terminal append. A failure log describes an observed
  failure even if the following terminal append itself fails. Logs do not
  replace the durable ledger or prove publication.
- Logs use a 16-hex-character SHA-256 `run_ref` rather than the caller's raw
  run ID, which otherwise permits arbitrary strings. Source diagnostics
  similarly use a `source_ref` digest of the source alias. This gives stable
  correlation without copying arbitrary aliases, URLs, tokens, or config.
  The ledger's existing identities and semantics remain unchanged.
- Ingestion logs scrape start, returned count, write start, commit start,
  and committed counts. The final message occurs only after the source's
  existing session context exits successfully. Failure counters for
  inserted/skipped rows remain zero after rollback. No per-article logs
  were added. A duplicate-only source honestly has yield > 0, insert = 0.
- Resolver logs cover lock acquisition, pending flush, initialization,
  context/mention/constraint loads, grouping/partitioning, constraint
  remapping, blocking, version registration, scoring, review enqueueing,
  clustering, retraction, entity/evidence writes, and the final commit.
  Counts come from a fixed allowlist of non-negative integer fields.
  Each phase emits at most a start and a terminal log, independent of the
  number of mentions, pairs, or rows. A completed write phase describes
  work inside a transaction, not durability; only the separate commit
  success follows transaction exit.
- `time.monotonic()` measures durations; UTC ledger timestamps are
  unaffected. No background thread, periodic heartbeat, or per-row clock
  check is added. Abrupt process death may leave an unmatched start, which
  identifies the last reached boundary without inventing a failure result.
- Existing exception logging in the touched runtime/ingestion boundaries is
  replaced with closed reasons because exception messages can contain SQL
  parameters, response bodies, or credentials. Exception propagation and
  chaining remain unchanged. This is not a repository-wide logging audit.

### Empty-constraint fast path

When `cannot_links` is empty, return one sorted partition (or no partitions
for an empty member list). Preserve the original duplicate-ID rejection
with a linear adjacent-ID check after sorting. Sorting preserves the same
representative and within-group order. There are no pair membership probes.

When the set is nonempty, execute the unchanged greedy algorithm, even when
its entries are irrelevant to this particular group. Human cannot-links,
must-links, deterministic representatives, gazetteer decisions, scorer
thresholds, clustering, review limits, and append-only provenance are not
relaxed. The resolver version is unchanged because outputs are equivalent.

## Consequences and verification

The empty case costs sorting plus a linear validation pass instead of a
quadratic membership loop. Controlled before/after counts and timings are
recorded in `benchmarks/results.md`. Reproduce them without database/network
access:

```sh
python -m scripts.bench_resolution_partition --sizes 1000 5000 10000 --repeat 3
```

The preserved baseline helper is used for property-based equivalence tests
with empty and nonempty constraints. A full-resolution test compares entity
names, properties, mention membership, evidence provenance, and key counters
before/after, both with and without a human cannot-link. Tests also cover
empty/singleton groups, duplicate-ID rejection, unchanged nonempty probes,
monotonic timing, secret/error redaction, duplicate-only ingestion, write and
commit failures, and interrupted stages. Tests assert work counts and output
equality, never a wall-clock speed threshold.

These logs are local runner diagnostics. They do not prove that the scheduled
production cancellations are fixed, and no production rerun was made for
this change. A later authorized run can identify whether time is dominated
by scraping, writing/commit, context loading, partitioning, or evidence
writes. PostgreSQL/R2 timing is not represented by an in-memory unit suite.

No schema, dependency, production workflow, source budget, article cap, lease,
release assets, or publication behavior changes. The existing scheduled
55-minute timeout, 50-minute lease, 20-article cap, and 90-second source budget
remain intact. The full recompute remains; it is not a no-op resolver run.

## Python notes for explaining the change

`@contextmanager` turns a generator with one `yield` into a `with` block.
Code before `yield` records the start; code after it records success. If the
body raises, the exception is delivered at `yield`, where it can be logged
safely and re-raised unchanged. A `with` block does not introduce a variable
scope in Python, so the pipeline variables remain available in later phases.
The yielded dictionary is only a small collection of numeric diagnostics.
