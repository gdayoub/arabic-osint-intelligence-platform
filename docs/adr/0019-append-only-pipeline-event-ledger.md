# ADR 0019: Pipeline health is projected from append-only events

## Status

Accepted — 2026-08-20

## Context

The scheduled pipeline previously returned counters to its caller but did not
leave a durable account of which run, stage, or source succeeded.  A single
mutable `pipeline_runs` row with `status = 'running'` and a later update to
`'succeeded'` looks simple, but it lies in exactly the case health tracking is
meant to catch: a killed runner cannot execute its final update.  It also loses
the sequence of retries and makes duration or count regressions harder to
audit.

Source health needs more precision than "inserted zero rows."  A scraper can
yield ten already-known articles and insert zero healthy rows, or a broken
selector can yield zero articles.  Those are different states.  Public health
also cannot include arbitrary exception text because a traceback, request URL,
or response body may contain credentials or private data.

This slice establishes run/stage/source truth.  Immutable release payloads,
promotion ordering, and the `publication_state` high-water projection belong
to the next F0.4 release slice and are not implemented here.

## Options considered

1. **One mutable row per run.** Rejected because a dead process cannot mark
   itself dead, updates erase history, and concurrent stages contend on one
   row.
2. **Use GitHub Actions logs as the ledger.** Rejected because retention is
   external, source counters are not queryable with the application data, and
   logs are not a stable public-health contract.
3. **Append immutable events and project health — chosen.** Each observation
   is durable by itself.  A separate monitor can append abandonment after a
   lease expires, and current status/duration is a deterministic projection.

## Decision

`pipeline_events` stores one immutable observation with a stable, unique
`event_key`, run/release identifiers, commit SHA, UTC event time, optional
stage/source scope, relevant counters, extractor versions, lease expiry, latest
successful article time, and one optional closed-vocabulary reason code.

The event vocabulary covers run start/heartbeat/terminal events, stage
start/terminal events, source start/terminal events, abandonment, and the full
release lifecycle (`release_reserved`, `release_candidate_created`,
`promotion_started`, `release_published`, `release_failed`, and
`release_superseded`).  Recording the lifecycle vocabulary in the initial
ledger migration avoids an immediate CHECK-constraint rewrite. The write
service validates
scope and ordering: every non-start event needs a run start; terminal stage and
source events need their corresponding start; source events need their parent
stage; and duplicate starts or terminals are refused.  Replaying the exact
same `event_key` and payload returns the original row.  Reusing a key with new
content fails rather than rewriting history.

Source terminal events record both `output_count` (articles yielded) and
`inserted_count` (new database rows).  Zero yield is explicit and degraded;
zero insertion with a positive yield is not automatically a failure.  Attempt,
selector-failure, parse-failure, and latest-successful-article measurements
remain separately queryable.

The scraper keeps the observations distinct before a source event is written:
listing attempts, fetched listings with no valid article link, article-page
attempts, yielded articles, and insertions are separate counters.  A fetched
listing that produces no valid article link counts as a selector failure because
empty matches, rather than Python exceptions, are the normal failure mode when
site markup changes.  Article parser failures are caught per article, so one
malformed page does not discard successful articles from the same source run.
Only the corresponding closed `PipelineReasonCode` leaves the ingestion
boundary; exception text remains in private logs.

Failure explanations use `PipelineReasonCode`.  The database check constraint
and Python write path reject arbitrary strings.  The health serializer maps
each code to one prewritten sentence and never accepts or interpolates an
exception.  Debugging detail remains in private runner logs.

Run, stage, and source status plus duration are rebuilt from ordered events by
pure projection functions.  A started run is `running`, an expired but not yet
monitored lease is `overdue`, and the monitor appends `run_abandoned` so a
killed process becomes permanent audit history.  A terminal success with an
unfinished child is `partial`, never healthy.  Source failures make health
failed; zero yield, partial parsing, or configured staleness make it degraded.

PostgreSQL revision `0002_pipeline_event_ledger` installs a `BEFORE UPDATE OR
DELETE` trigger that raises for raw SQL as well as ORM writes.  SQLAlchemy
mapper guards provide the same normal ORM behavior in SQLite unit tests.  Raw
SQLite SQL is not treated as the production security boundary.  The migration
is forward-only because dropping the table would delete audit history.

The mutable `publication_state` table and immutable candidate/promotion rules
are specified separately in ADR 0020. No workflow or public JSON is wired to
the ledger yet, so the existing public contract remains unchanged.

## Consequences

- A killed runner can be identified honestly by a later process without
  editing the dead run's history.
- Stage durations, stage counts, source yield, source insertions, and failures
  are queryable for regression comparisons.
- Public health can explain a category of failure without exposing stack
  traces, credentials, or response bodies.
- Event history grows rather than being overwritten.  This is intentional and
  may eventually need retention/partition measurements; no optimization lands
  before those measurements exist.
- Projection code is more work than reading one status column, but it is pure,
  deterministic, and can be rebuilt after code changes.
- SQLite catches ORM mutation attempts but does not reproduce the PostgreSQL
  trigger boundary.  PostgreSQL 16 migration CI is the authoritative check.
- Rollback is a forward repair or database restore, never a destructive
  downgrade that silently removes operational evidence.
