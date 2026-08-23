# ADR 0032: Explicit scheduled and backfill runtime budgets

## Status

Accepted — 2026-08-23

## Context

Before this change, `run_core_pipeline` / `build_core_pipeline_stages` /
`run_core_ingestion` had one implicit article cap per source
(`SETTINGS.max_articles_per_source`, defaulting to 120), and nothing in the
CLI, the workflow, or the ledger distinguished "this is the recurring cron
run" from "someone manually asked for a bigger catch-up scrape." Both cases
called the same command with the same default.

Task 1's baseline (`benchmarks/intelligence_atlas_baseline.md`,
`docs/execution/intelligence-atlas/tranche-a.md`) recorded two facts that
make that implicit default risky going forward:

- `pipeline.yml`'s scheduled job has a hard 30-minute `timeout-minutes`, and
  the platform now configures 21 sources (`build_scrapers()` in
  `src/pipeline/ingest_core.py`), not the handful it had when 120/source was
  chosen.
- Only 3 of the 21 configured sources currently reach the public snapshot at
  all — some sources are already marginal within the existing timeout at the
  existing cap, before Task 4's per-source budget enforcement
  (`IngestionBudget`, tranche-a step 4) exists to protect the run as a whole.

A cron run that silently inherits a "give me lots of data" default is exactly
the kind of run that can blow through the 30-minute ceiling and end the whole
scheduled run failed, with none of its sources landing. A human running a
deliberate backfill, on the other hand, legitimately wants more than 24
articles per source and is willing to wait longer for it — but that run must
never be able to accidentally publish a partially-verified or oversized
scrape to the public dashboard the way the scheduled workflow does.

## Decision

Introduce `PipelineMode` (`src/config/settings.py`):

```python
class PipelineMode(str, Enum):
    SCHEDULED = "scheduled"
    BACKFILL = "backfill"
```

with two named budgets, `SCHEDULED_LIMIT_PER_SOURCE = 24` and
`BACKFILL_LIMIT_PER_SOURCE = 120`, and one validation/defaulting function,
`resolve_limit_per_source(limit_per_source, *, pipeline_mode)`. It is the
single place that:

- defaults `None` to the mode's budget (24 scheduled, 120 backfill);
- rejects any explicit non-positive or non-integer value with `ValueError`.

`main.py`'s CLI, `run_core_pipeline`, `build_core_pipeline_stages`, and
`run_core_ingestion` all call this one function (directly, or through
`src/ops/runtime.py`'s `_resolved_limit_per_source` wrapper, which only
translates `ValueError` into the runtime module's existing
`RuntimeConfigurationError`) instead of re-implementing the check. `main.py
run-core-pipeline` gains `--pipeline-mode {scheduled,backfill}` (default
`scheduled`) and `--limit-per-source` (default: mode's budget); an invalid
`--limit-per-source` fails argument parsing before any run event is
attempted. `build_core_pipeline_stages` resolves the cap eagerly, before
building the ingest stage's closure, so an invalid cap passed programmatically
also fails before `run_orchestrated_pipeline` ever calls `recorder.start()`
i.e. before any `run_started` ledger event is written.

The resolved `pipeline_mode` and `limit_per_source` are recorded as part of
the existing config/component fingerprint returned by
`core_component_versions()` — `ingest.pipeline_mode` and
`ingest.limit_per_source` join the scraper/classifier/extractor/resolver
version entries already captured in `run_started.extractor_versions`. A run's
mode and cap are exactly the kind of "what configuration produced this
health projection" evidence that field already exists to answer.

### 24 as the starting scheduled budget

24 articles/source × 21 sources is chosen to fit comfortably inside
`pipeline.yml`'s existing 30-minute timeout with headroom for process,
extract, resolve, and bake, given that only a minority of sources are
currently confirmed to reach the public snapshot at all under the *previous*
120/source cap within that same window. 24 is a starting point, not a
measured optimum — Task 4's tranche-a work (`IngestionBudget`, per-source and
global time budgets, `SourceCheckpointORM` watermarks) is the mechanism that
is expected to let this number be revisited with real per-source timing
evidence instead of the conservative baseline used here. Any change to
`SCHEDULED_LIMIT_PER_SOURCE` should cite that benchmark, not just adjust the
constant.

### Why backfill never publishes automatically

`.github/workflows/backfill.yml` is `workflow_dispatch`-only (no `schedule:`
trigger), requires a `reason` string input, shares the scheduled workflow's
`osint-neon-writer` concurrency group with `cancel-in-progress: false` (so a
backfill and a scheduled run serialize instead of racing the same
URL-unique inserts), and has a 120-minute timeout to accommodate the larger
120/source default. It runs the identical pre-write gates the scheduled
workflow runs — `generate_dashboard_contract.py --check`, the full
`tests/unit` suite, and `verify_core_schema.py` — before invoking
`main.py run-core-pipeline --pipeline-mode backfill`.

It then stops. There is no `cp src/api/static/*.html`, no
`cloudflare/wrangler-action` deploy step, and its bake output path
(`dist/backfill-data.json`) is deliberately different from the scheduled
workflow's `dist/data.json` so a backfill's snapshot can never be picked up
by a later step that assumes the scheduled path. A manual, potentially
larger and less-curated scrape reaching the public dashboard without a human
explicitly deciding to publish it would be a bigger blast radius than a
degraded scheduled run — ADR 0026 already drew this same line for the
scheduled pipeline (ledger evidence is not a publication claim); this ADR
draws it more strictly still for backfill, which has no deployment step to
draw the line at in the first place.

`pipeline.yml`'s `workflow_dispatch` also gained `pipeline_mode` and
`limit_per_source` inputs, but `pipeline_mode` is a `choice` input whose
`options:` list contains only `"scheduled"` — `backfill` is not a selectable
value there at all, because that workflow ends in an unconditional
`cp src/api/static/*.html` + `cloudflare/wrangler-action` deploy with no
`if:` guard on `pipeline_mode`. A choice input restricted to one option, plus
the run step hardcoding `--pipeline-mode scheduled` as a literal rather than
reading `inputs.pipeline_mode` at all, means there is no value anywhere in
that workflow's dispatch form that reaches the deploy step as a backfill run
— not merely a default that steers away from one. `limit_per_source` is still
exposed there, but only as a cap override for an explicitly-still-scheduled,
explicitly-still-published run; the input's own description says as much.
The cron `schedule:` trigger has no `inputs` context in the first place, so a
cron firing never reads either input regardless.

## Consequences

- The scheduled pipeline's cap is now an explicit, ledgered fact
  (`ingest.pipeline_mode=scheduled`, `ingest.limit_per_source=24`) instead of
  an implicit `SETTINGS.max_articles_per_source` read with no record of what
  ran.
- A human who needs more than 24 articles/source has exactly one sanctioned
  path — `backfill.yml`, a larger, longer, and explicitly non-publishing
  run — and cannot reach it, nor anything resembling it, through
  `pipeline.yml` or the cron trigger.
- `resolve_limit_per_source` is the only place "positive integer, defaulted
  by mode" is checked; the CLI, the ledgered runtime, and direct
  `run_core_ingestion` callers all route through it (or its thin
  exception-translating wrapper in `src/ops/runtime.py`), so a future change
  to the validation rule needs one edit, not three.
- 24 remains a conservative placeholder pending Task 4's real per-source
  timing budget; it is expected to change, deliberately, with evidence.

## Recovery

Revert the code/workflow commit. No schema or migration change is involved:
`ingest.pipeline_mode` and `ingest.limit_per_source` are ordinary
`extractor_versions` map entries on the existing append-only
`PipelineEventORM` table, not a new column or constraint. If a scheduled run
under the new default behaves poorly, dispatch the prior commit's workflow
and keep the current immutable public release pinned, per the existing
release-recovery procedure (ADR 0020 / ADR 0027).
