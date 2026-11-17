# ADR 0031: Prune the per-mention evidence of retracted entity generations

## Context

Entity resolution is a full recompute. Every scheduled run, `resolve_all`
retracts the previous generation of entities and writes a new one, adding one
`entity_mentions` row and one `provenance` row for every member mention.
P6 ("retract, never delete") kept every retracted generation forever.

That made storage grow by roughly one copy of the corpus's mention links per
run, about 25–30 MB. On 2026-10-20 the Neon project reached its 1 GB limit,
and every scheduled run after that failed on its first insert with
`DiskFull`. The public dashboard stayed frozen on that day's data for four
weeks.

Measured on 2026-11-17 with `scripts/db_size_report.py`:

| | entities | entity_mentions | provenance (entities) |
|---|---|---|---|
| live | 65 | 112,841 | 112,841 |
| retracted | 8,864 | 4,165,057 | 4,165,057 |

So 97% of the evidence rows described generations nobody reads. The
`provenance` and `entity_mentions` tables alone took 830 MB of the 1,002 MB
database.

## Decision

Before each pipeline run, `prune-retracted-evidence` deletes the
`entity_mentions` rows and the `target_table = 'entities'` provenance rows
that belong to **retracted** entities. Then it runs `VACUUM` so the space is
reused.

This deliberately and narrowly scopes an exception to P6:

- **Retracted entity rows stay.** Their ids, `supersedes_id` chains and
  retraction reasons remain on record.
- **History is kept elsewhere.** "What did we believe at time X" is answered
  by the stable-entity snapshot and membership tables (ADR 0029), which are
  compact and untouched.
- **Nothing else changes.** Documents, mentions, facts, translations, review
  decisions and their provenance are never pruned.
- **Nothing reads the pruned rows.** No foreign key references the pruned
  rows, and every reader already filters on `retracted = false`.

For the one-time cleanup, the Maintenance workflow runs the same command with
`VACUUM FULL`, which returns the space to the storage quota.

## Consequences

- Steady-state size is now bounded at about two generations of evidence
  instead of growing without limit.
- `provenance show` for a retracted entity no longer lists its member
  mentions. Its stable-entity membership history is the source of truth for
  that.
- The real fix for the write amplification is incremental resolution: keep
  an entity whose membership did not change instead of retracting and
  recreating it. That is a larger change to the generation lineage logic and
  is left for M6. Pruning makes the current design sustainable until then.
- `db-size-report` in the Maintenance workflow makes the next storage issue
  visible before it becomes an outage.
