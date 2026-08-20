# Core schema migrations

This Alembic history owns only the tables in `src.store.orm.CoreBase`. The
frozen `raw_articles` and `processed_articles` tables belong to the separate
legacy `src.database.models.Base` metadata and are intentionally invisible to
autogeneration and schema adoption.

## New, empty database

`init-core-db` is now an Alembic entrypoint rather than a `create_all()`
shortcut. It initializes only a genuinely empty core schema and verifies a
schema already at head:

```bash
DATABASE_URL='postgresql+psycopg2://...' python main.py init-core-db
```

It refuses an unversioned database that already has any Alembic-owned table,
and it refuses to advance an existing older revision. Those cases require the
explicit adoption or rollout path below. Frozen legacy `raw_articles` and
`processed_articles` tables may coexist and are not created, changed, or
removed by core migrations.

## Database created before Alembic

First perform a read-only audit:

```bash
DATABASE_URL='postgresql+psycopg2://...' python scripts/adopt_core_schema.py
```

If and only if the audit reports an exact match for all `CoreBase` tables,
stamp the existing schema at the baseline revision:

```bash
DATABASE_URL='postgresql+psycopg2://...' python scripts/adopt_core_schema.py --stamp
```

Stamping records history; it does not execute the baseline DDL. A missing,
extra, or changed core column, key, constraint, or index makes the command
refuse to stamp. Unrelated tables, including the two legacy tables, are left
alone.

The adoption target is the frozen metadata in
`src/store/schema_baseline_0001.py`, not today's `CoreBase`. Later ORM tables
and columns therefore cannot silently redefine revision 0001. The audit also
distinguishes unrelated tables from known post-baseline Alembic-owned tables:
if a table such as `pipeline_events` already exists without revision history,
adoption refuses because stamping 0001 and then running its later create-table
revision would collide. Reconcile that table and its data deliberately; do not
drop it merely to make the audit green.

Alembic 1.19.1's SQLite comparison detects table/column, unique-constraint,
foreign-key, and index drift, plus a missing named check. It does not detect
primary-key drift, and its default comparison treats two same-name check
constraints as equal without comparing their expressions. The adoption audit
therefore performs its own primary-key comparison and normalized check
comparison. The normalizer preserves literal values and recognizes
PostgreSQL's equivalent `IN (...)` to `= ANY (ARRAY[...])` reflection form.

## Production upgrade after adoption

Production upgrades are manual, exact revision-boundary operations. First
prepare and record a Neon recovery branch or point-in-time restore location.
Then dispatch `.github/workflows/schema-upgrade.yml`. For the first rollout,
the inputs are:

```text
expected_current: 0001_core_baseline
expected_target:  0003_publication_state
confirmation:     UPGRADE 0001_core_baseline TO 0003_publication_state
```

The workflow holds the same `osint-neon-writer` concurrency group as the
pipeline, review decisions, and maintenance. Before it connects to production
it upgrades isolated PostgreSQL 16 databases and runs the authoritative
migration tests. Production is then checked read-only for the exact current
revision and, for 0001, the frozen baseline shape. Apply rechecks those facts
inside the DDL transaction, preserves legacy table counts, advances only to
the repository's sole head, audits the reflected schema against `CoreBase`,
commits, reconnects, and verifies again.

Scheduled and manual data writers run `scripts/verify_core_schema.py`; they do
not initialize or migrate. If code and production revisions differ, the run
stops before the first data write. Runtime use of a newly added table lands
only after the schema workflow succeeds.

For a local read-only plan, omit `--apply`:

```bash
DATABASE_URL='postgresql+psycopg2://...' python scripts/upgrade_core_schema.py \
  --expected-current 0001_core_baseline \
  --expected-target 0003_publication_state
```

The migrations are forward-only. A PostgreSQL DDL failure rolls back the
transaction. If the schema commit succeeds but a later application problem is
found, keep the old code running against the additive schema and write a new
forward-repair revision. Restore the recorded Neon recovery point only when a
forward repair cannot preserve correctness; do not delete the event ledger or
publication state with an ad-hoc downgrade.

## Verification policy

SQLite runs the fast empty-to-head and existing-schema-adoption tests. The
required `migration-postgres` CI job repeats empty-to-head, exact-baseline,
adoption, structural-drift, and append-only-trigger checks against a disposable
PostgreSQL 16 database. PostgreSQL is authoritative for JSONB, transactional
DDL, constraint reflection, and identifier behavior; passing SQLite alone is
not production migration approval. The local PostgreSQL normalization fixture
covers its known reflected membership expression but is not a substitute for
that server-backed CI job.
