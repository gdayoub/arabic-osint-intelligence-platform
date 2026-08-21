# ADR 0023: Record pipeline runtime before enabling it to publish

## Status

Accepted — 2026-08-21

## Context

The core pipeline already has useful operations for ingestion, processing,
extraction, resolution, optional title translation, and dashboard baking.  It
did not yet connect those operations to the append-only operational ledger.
Adding direct ledger calls into every pipeline module would make each domain
operation responsible for event ordering, leases, failure handling, and safe
error serialization.  It would also make the first runtime activation and the
schema rollout one inseparable change.

The release layer can create immutable candidate bytes, but promotion has a
separate external deployment boundary.  A candidate is not evidence that a
dashboard is live.

## Options considered

1. **Let every pipeline function append its own events.** Rejected because
   duplicate stage/source lifecycle rules would spread across unrelated domain
   modules and one missed exception path could leave a run looking healthy.
2. **Enable the new runtime in the scheduled workflow immediately.** Rejected
   because production is deliberately being moved to the ledger schema first
   under ADR 0021's revision-boundary rollout.
3. **Create one dormant orchestration service — chosen.** It wraps existing
   operations, writes durable boundary events, and is exposed as a callable
   module for a later explicit CLI/workflow activation.

## Decision

`src/ops/runtime.py` provides `PipelineStage`, closed `StageOutcome` and
`SourceOutcome` values, and `run_orchestrated_pipeline`.  It records
`run_started`, lease heartbeats, stage start/terminal events, and source
start/terminal events through separate session scopes.  A stopped runner
therefore leaves enough durable information for the existing lease monitor to
mark it abandoned.

The ingestion adapter consumes only the existing closed telemetry fields:
attempts, yield, inserts, parser/selector failures, latest successful article
time, and an approved reason code.  Optional callbacks at the individual
scraper boundary record source start and terminal events at the time that
source actually runs, rather than assigning every source the batch duration.
They reject URLs and arbitrary diagnostics as source aliases and never write
exception strings to the event stream.  Zero yielded articles remains distinct
from positive yield with zero inserts.

An actual exception closes the current stage and run as failed with a safe
reason.  A completed but failed source also closes its parent stage and run;
a degraded source or non-source item error completes the stage but makes the
health projection degraded.  If a caller asks for a release candidate, the
service requires every completed stage and source to be healthy.  Otherwise it
records a failed `prepare_release` stage and no release reservation is made.

When allowed, candidate preparation calls only `create_release_candidate`.
The bake adapter includes its `data.json`, every generated country JSON page,
and byte-for-byte copies of the existing static deployment shell:
`src/api/static/dashboard.html` as `index.html` and
`src/api/static/country.html` as `country.html`.  That mirrors the current
workflow's `dist` contents without modifying either UI file.  It records
immutable candidate evidence and returns its manifest identity.  It does not
import or call `promote_release`, a publisher, a Cloudflare worker, or a
deployment workflow.  This intentionally produces no `release_published`
event and makes no claim that public data changed.

`main.py` and GitHub Actions remain untouched at this checkpoint.  They can
adopt the service only after the explicit production schema upgrade completes
and its checks are green.

## Consequences

- The normal pipeline remains behaviorally unchanged until an explicit future
  activation; the code is testable now without writing production events.
- Run health gets one authoritative event order rather than hand-coded stage
  bookkeeping in six domain modules.
- Candidate creation is safe to demonstrate and inspect without accidentally
  deploying an unverified bundle.
- The operational service adds a small adapter layer for current stat shapes.
  New stages must return `StageOutcome` rather than leaking arbitrary dicts.
