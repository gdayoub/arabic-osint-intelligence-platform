# ADR 0017: Alembic owns revisioned changes to the core schema

## Status

Accepted — 2026-08-20

## Context

`init-core-db` currently calls `CoreBase.metadata.create_all()`. That is useful
for an empty local or Neon database, but it only creates missing tables. It
cannot add a column to an existing table, backfill it, enforce a later
constraint, or tell us which schema revision production is running.

M5 and M6 require real schema changes. Applying those changes as loose SQL in
the scheduled six-hour pipeline would mix deployment with ingestion, make
partial releases difficult to reason about, and provide no safe way to prove
that a pre-existing Neon schema matches the revision we claim it does.

The database also contains frozen `raw_articles` and `processed_articles`
tables mapped by a separate SQLAlchemy `Base`. A migration tool must not infer
that those unrelated tables should be removed merely because they are absent
from `CoreBase`.

## Options considered

1. **Continue using `create_all()`.** Rejected because it is initialization,
   not migration, and silently leaves existing tables behind the ORM.
2. **Keep numbered handwritten SQL files.** This avoids a dependency, but we
   would have to build revision tracking, ordered execution, offline SQL,
   dialect handling, and schema comparison ourselves.
3. **Adopt Alembic 1.19.1 for `CoreBase` only — chosen.** Alembic is the
   standard migration companion to the SQLAlchemy version already in use. It
   adds revision tracking and schema comparison without entering the runtime
   query or pipeline hot path. The cost is one pinned dependency plus a small
   migration environment that the team must understand.

## Decision

`migrations/env.py` exposes only `src.store.orm.CoreBase.metadata`. Reflected
tables outside that metadata are ignored by audit/autogeneration, preserving
the frozen legacy tables.

Revision `0001_core_baseline` explicitly recreates the schema that existed
immediately before Alembic: every current core table, column, key, named check,
unique constraint, and explicit index. It contains no new product field.

There are two deliberate adoption paths:

- A new empty database runs `alembic upgrade head` explicitly.
- A database created by `init-core-db` first runs the read-only
  `scripts/adopt_core_schema.py` audit. `--stamp` is allowed only after Alembic
  plus the explicit structural checks report no difference from the frozen
  revision-0001 metadata, and only when no different revision is already
  recorded. Stamping runs no baseline DDL and changes no evidence row.

The adoption target is stored separately in
`src/store/schema_baseline_0001.py`. It must never follow later `CoreBase`
changes: revision 0001 has one historical meaning. A regression test upgrades
an empty database to revision 0001 and compares it with that frozen metadata.
`audit_head_schema()` has the separate job of comparing a database upgraded
to head with today's live `CoreBase`; the two audits cannot substitute for one
another.
Known post-0001 tables from the live migration-owned metadata are a separate
guard. If one already exists in an unversioned database, adoption refuses
rather than stamping 0001 and colliding when its later create-table revision
runs. Unrelated and frozen legacy tables remain ignored.

Alembic 1.19.1 was probed against drifted SQLite schemas. It detects missing
unique constraints, foreign keys, indexes, and named checks, but does not
detect primary-key drift. Its default same-name check comparison also assumes
the two expressions are equal. The adoption audit therefore owns explicit
primary-key signatures and check signatures. Check SQL is normalized without
altering string literals, including PostgreSQL's reflected rewrite of
`value IN (...)` to `value = ANY (ARRAY[...])`.

Production never migrates during the scheduled scrape. Each future schema
change follows expand, idempotent migrate/backfill, then contract in an ordered
release operation. The operator records a backup and current revision before
apply, verifies old code still runs after expand, and promotes application
code only after the migration succeeds. A failed destructive change is
handled by forward repair or database restore, not by assuming downgrade is
safe. For that reason the evidence-bearing baseline has no destructive
downgrade implementation.

`init-core-db` remains compatible for now because the current pipeline still
needs to initialize a genuinely empty database. Once production is stamped
and the release workflow owns schema apply, a later checkpoint can retire
that scheduled `create_all()` call. This ADR does not auto-migrate or touch
production.

## Consequences

- Schema state becomes explicit and reviewable before M5/M6 fields land.
- An adoption mistake fails closed: missing or drifted core objects refuse the
  stamp; unrelated legacy tables remain untouched. A post-baseline managed
  table without matching revision history also refuses the stamp.
- Alembic is installed wherever `requirements.txt` is installed even though
  it is used only for release/schema operations. That small cost avoids a
  second dependency file and ensures migration commands use the same pinned
  SQLAlchemy driver stack as CI.
- SQLite tests quickly prove empty-to-head and existing-schema adoption
  semantics. The required `migration-postgres` CI job repeats head, baseline,
  adoption, structural-drift, and database-trigger checks against PostgreSQL
  16, which is authoritative for JSONB, transactional DDL, constraint
  reflection, and identifier behavior. A local unit fixture covers
  PostgreSQL's known reflected `ANY (ARRAY[...])` check form but does not
  replace that server-backed gate.
- The baseline's explicit operations duplicate the ORM definition. The
  duplication is intentional historical evidence: future ORM changes must be
  accompanied by a new revision rather than editing this baseline.
- The frozen baseline metadata duplicates the revision once more for safe
  adoption comparison. An empty-to-0001 test prevents those two historical
  definitions from drifting; neither file changes after the baseline lands.
