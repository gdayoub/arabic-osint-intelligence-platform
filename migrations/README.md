# Core schema migrations

This Alembic history owns only the tables in `src.store.orm.CoreBase`. The
frozen `raw_articles` and `processed_articles` tables belong to the separate
legacy `src.database.models.Base` metadata and are intentionally invisible to
autogeneration and schema adoption.

## New, empty database

Run the upgrade explicitly:

```bash
DATABASE_URL='postgresql+psycopg2://...' alembic -c alembic.ini upgrade head
```

No scheduled workflow runs this command. Production migration is a separate,
ordered release operation with a backup and a recorded before/after revision.
After upgrade, `audit_head_schema(connection)` compares the database with the
live `CoreBase` metadata. This is intentionally different from the frozen
adoption audit below.

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

`python main.py init-core-db` remains available during this transition for
the current scheduled pipeline and fresh local databases. It is not a schema
migration mechanism and must not be used to apply future revisions.

## Verification policy

SQLite runs the fast empty-to-head and existing-schema-adoption tests. The
required `migration-postgres` CI job repeats empty-to-head, exact-baseline,
adoption, structural-drift, and append-only-trigger checks against a disposable
PostgreSQL 16 database. PostgreSQL is authoritative for JSONB, transactional
DDL, constraint reflection, and identifier behavior; passing SQLite alone is
not production migration approval. The local PostgreSQL normalization fixture
covers its known reflected membership expression but is not a substitute for
that server-backed CI job.
