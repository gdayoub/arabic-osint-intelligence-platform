# Tranche A — ingestion floor

Source of record:
`docs/superpowers/plans/2026-08-22-intelligence-atlas-implementation.md`
(the full 48-task, 9-tranche plan). This file summarizes Tranche A only, so a
later task's implementer does not have to re-read the whole plan just to
place Task 1 in context. If this summary and the plan file ever disagree,
the plan file wins — update this file, not the other way around.

Tranche A is six tasks that establish an honest ingestion floor before any
Intelligence Atlas feature work (bilingual search, the graph, events,
stories, risk scoring, the new UI) is allowed to depend on the pipeline's
output. Nothing downstream should assume more coverage or more timing
headroom than Tranche A actually proves.

## Tasks

1. **Freeze the baseline and write the capability matrix** (this task).
   Groundwork only: record exact git/migration/contract state, run the real
   baseline commands and paste their real output, and build a machine-
   checked capability matrix so later tasks have a fixed point instead of a
   moving memory of what the pipeline currently does. No pipeline behavior
   changes.
2. **Add explicit scheduled and backfill ingestion modes.** Introduces
   `PipelineMode` (`scheduled` vs `backfill`) with different per-source
   article caps (24 vs 120), threaded through `main.py`,
   `build_core_pipeline_stages`, and `run_core_ingestion`. A cron run can
   never accidentally inherit the larger manual backfill cap.
3. **Reconcile and finish the private bilingual foundation.** Completes
   already-in-progress work: migration `0006_bilingual_derived_data`,
   `TranslationArtifact`/`StableEntityVariant`/`TranslationUse` records,
   content-addressed translation caching. Must land as the single Alembic
   head before Task 4 assigns `0007`.
4. **Add stable source checkpoints and enforce the ingestion budget.**
   Migration `0007`, a `config/source_registry.yaml` seeding exactly the 21
   configured outlets with stable `outlet_uid`s, append-only
   `SourceCheckpointORM` watermarks, and a hard per-source/global time
   budget (`IngestionBudget`) so one slow source cannot consume the whole
   scheduled job.
5. **Prove the full-job budget and warm the historical baseline.** Three
   independent non-publishing benchmark runs proving the data pipeline
   stays under 19 minutes and the full GitHub Actions job under 25 (the
   workflow's hard timeout stays 30 — see the risk register in
   `benchmarks/intelligence_atlas_baseline.md`), plus a guarded 60-day
   backfill and a source-by-day coverage matrix.
6. **Run the 21-source production checkpoint.** Two scheduled confirmation
   runs against production, then update this baseline's counts
   (`benchmarks/intelligence_atlas_baseline.md`) and `docs/AGENT_BRIEF.md`
   with the now-verified numbers.

## Why Task 1 has to run first

Tasks 2-6 all modify runtime behavior (ingestion caps, checkpoints, budgets,
production runs). None of them can be judged as an improvement — or even
correctly scoped — without a truthful "before" snapshot. Task 1 is that
snapshot: it does not change what the pipeline does, only what is recorded
about it.

## Known gap this baseline carries forward

At Task 1 time, this environment has no Neon/pipeline-ledger database
access, so `tests/fixtures/runtime/source_capability_matrix.json` records
`live_attempted`, `body_paragraphs_preserved`, `published_date_quality`,
`robots_policy_checked`, `state_media_metadata`,
`last_attempt_duration_seconds`, `last_yield`, and `last_failure_reason` as
`null` for every source rather than guessing from configuration. Task 4's
`SourceCheckpointORM` and Task 6's production checkpoint are the tasks that
are expected to make these fields real; whichever task first gets ledger
query access should regenerate the fixture from real data rather than
hand-filling these nulls.
