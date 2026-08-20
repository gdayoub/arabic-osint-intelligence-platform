# ADR 0021: Production schema changes are explicit revision-boundary rollouts

## Status

Accepted — 2026-08-20

## Context

ADR 0017 introduced Alembic and deliberately left the scheduled
`init-core-db` call in place until production adopted revision 0001.
Production is now audited and stamped at `0001_core_baseline`, and revisions
0002 and 0003 add the pipeline event ledger and publication coordination
state. Leaving `CoreBase.metadata.create_all()` in any production path would
let a writer create those tables without recording either migration. A later
Alembic apply would then collide with tables whose history it does not own.

The schema must land before runtime code writes operational events or reserves
releases. At the same time, schema apply must not overlap the six-hour
pipeline, an entity-review decision, or a maintenance repair. The database
still contains frozen legacy article tables that Alembic must ignore.

## Options considered

1. **Upgrade automatically at the start of every pipeline.** Rejected because
   ingestion and DDL would share one failure domain, and a newly pushed
   migration would touch production without an explicit revision review.
2. **Keep `create_all()` for missing tables, then stamp them.** Rejected
   because stamping after the fact cannot prove that constraints, indexes,
   triggers, or backfills match the migration that supposedly ran.
3. **Run Alembic manually from a laptop.** Rejected because the tested commit,
   exact inputs, logs, and writer serialization would be easy to lose.
4. **Use a manual GitHub Actions rollout with fail-closed checks — chosen.**
   It reuses the existing secret boundary and the shared writer queue while
   keeping schema apply separate from application work.

## Decision

`.github/workflows/schema-upgrade.yml` is the only production schema-advance
path. The operator names the exact current revision and exact target, records
a prepared Neon branch/PITR recovery reference, and types
`UPGRADE <current> TO <target>` exactly. The target must equal the repository's
single Alembic head. Partial revision identifiers, unexpected database heads,
branches, merge paths, backwards paths, and no-op applies are refused.

Before production access, the workflow runs the migration unit suite and the
PostgreSQL 16 integration suite against disposable databases. It then plans
against production without changing it. Apply repeats the checks inside one
transaction, snapshots the presence and row counts of `raw_articles` and
`processed_articles`, upgrades to the approved head, compares the resulting
schema with `CoreBase`, and checks that the legacy snapshot is unchanged.
After commit it reconnects and verifies revision plus reflected schema again.

All Neon writer workflows share `osint-neon-writer` with the rollout and run
`scripts/verify_core_schema.py` before their first data change. They never run
DDL. `init-core-db` remains convenient for an empty local database, but now
runs Alembic from `base` to head. On an existing old or unversioned core schema
it refuses and points to adoption or the explicit rollout instead of calling
`create_all()`.

The rollout order is schema first, runtime second:

1. merge additive migrations, rollout tooling, and writer verification while
   the new runtime paths remain dormant;
2. confirm the normal and PostgreSQL migration CI jobs;
3. prepare a Neon recovery reference and dispatch the exact production
   revision transition;
4. require the reconnect-at-head verification to pass;
5. only then merge or enable code that writes the new tables.

## Consequences

- A scheduled run that races a new migration commit may stop at the schema
  guard until the rollout completes, but it cannot partially write against a
  mismatched schema.
- PostgreSQL transactional DDL returns production to the previous revision if
  0002 or 0003 raises before commit. The workflow reports failure and runtime
  integration stays disabled.
- The current revisions are additive, so old application code remains usable
  after schema apply. If a defect appears after commit, pause writers and ship
  a forward-repair migration. The ledger and publication migrations reject
  destructive downgrade; restore the recorded Neon recovery point only when
  forward repair cannot preserve correctness.
- A recovery reference is evidence that a restore route was prepared, not an
  API-level proof that the named Neon branch exists. The operator is still
  responsible for creating it before dispatch.
- Legacy article tables remain outside Alembic ownership. A changed presence
  or row count during a core rollout aborts the transaction.
