# ADR 0026: Activate the run ledger without granting publication authority

## Status

Accepted — 2026-08-21

## Context

ADR 0023 introduced the durable runtime and deliberately left it dormant until
the production schema rollout was complete.  The scheduled pipeline still
performed its data work as several independent CLI commands, so it could not
write one truthful run, stage, and source lifecycle.  It also handled optional
DeepL translation with a GitHub Actions `continue-on-error`, leaving a provider
failure outside the durable health history.

The repository already has a separate candidate/promotion design in ADR 0020,
but the live Cloudflare deployment remains the existing direct `dist` deploy.
There is no provider observation in that path.  Recording `release_published`
or even creating a candidate from this activation would therefore overstate
what the runner knows.

ADR 0025 is owned by the M4.2 identity work.  This independent runtime
activation is recorded as ADR 0026.

## Decision

`main.py run-core-pipeline` is the sole scheduled data-work entrypoint.  It
runs ingestion, processing, extraction, resolution, optional translation, and
the dashboard bake through `run_core_pipeline` with:

- an explicit GitHub run/attempt ID and checkout SHA;
- a finite run lease for later abandonment monitoring;
- static scraper, classifier, extractor, resolver, and (when used)
  translator versions captured in `run_started` before source writes begin;
- `prepare_release=False`, no release ID, and no promotion or provider call.

The workflow keeps its existing `cp` commands and direct Cloudflare deploy
after the ledgered CLI returns.  Those steps are intentionally not part of the
ledgered command.  A `run_succeeded` event means the data stages completed; it
does **not** mean a static-site deployment completed, and this activation
emits no `release_*` or promotion events.

Translation has two explicit modes:

- `skip` omits title translation when no DeepL key is configured.
- `best-effort` runs title translation when the key is configured.  A provider
  exception is retained only in private job logs; the translate stage records
  a safe non-zero error count, the run becomes `degraded`, and baking proceeds
  with the usable Arabic data.

Hard source failures retain the existing fail-closed behavior: the ingest
stage and run close as failed, and process/extract/resolve/translation/bake do
not run.  Parser and selector warnings remain completed degraded source
observations, preserving the per-source behavior established in ADR 0023.

`main.py reconcile-pipeline-runs` is the only monitor command added here.  A
scheduled workflow uses the existing `osint-neon-writer` queue, verifies the
schema, and invokes `abandon_expired_runs`.  It may append
`run_abandoned(lease_expired)` for an unfinished expired lease.  It never
retries a run, creates a candidate, reconciles a promotion, contacts
Cloudflare, or changes public deployment state.

## Failure and recovery behavior

- A normal stage exception writes safe failed stage/run terminals and exits
  non-zero.  The direct deploy step is consequently skipped.
- A hard source failure prevents the bake, so no new `dist/data.json` is sent
  to the existing deployment step.
- A translation-provider failure is the deliberately narrow exception: it
  writes a degraded, successfully terminal run and permits the bake/deploy
  path to continue with Arabic titles.
- If a runner disappears, its last heartbeat lease expires.  A later monitor
  appends one immutable abandonment fact rather than guessing success.
- The monitor is serialized with writers, so it cannot abandon a run while
  that writer still owns the shared GitHub concurrency slot.

If the ledger wrapper itself must be rolled back, restore the prior individual
data CLI steps in `pipeline.yml` and disable the monitor workflow.  Existing
append-only event facts remain historical evidence and are not deleted.  The
Cloudflare deployment boundary is unchanged by this activation, so no public
release rollback is required.  Database schema problems continue to use ADR
0021's forward-repair or prepared recovery procedure.

## Consequences

- Production data work now has durable, queryable run/source evidence and
  version/SHA traceability without changing the dashboard contract or UI.
- Translation outages are visible as degraded rather than silently hidden by
  Actions-only behavior.
- The project still has no trustworthy assertion about which release is live;
  immutable candidate preparation, promotion, provider observation, and
  release reconciliation remain intentionally inactive future work.
