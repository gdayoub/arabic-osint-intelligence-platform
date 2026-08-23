# Intelligence Atlas — execution docs

This directory holds the execution record for the "Intelligence Atlas"
rebuild: turning the dashboard from a counts-and-charts view into an
evidence-first system, without breaking the existing pipeline, contract, or
portfolio consumer along the way.

Task 1 ("Freeze the baseline and write the capability matrix") is pure
groundwork. It writes down what was objectively true about the pipeline on
2026-08-22/23, before any Intelligence Atlas code exists, so that later
tasks have a fixed point to diff against instead of a moving memory of "what
the dashboard used to show."

## What's in this directory

- `README.md` — this file.
- `tranche-a.md` — the Tranche A task list this baseline sits in front of,
  and how each later task should use the baseline.

## What's next to this directory

- `benchmarks/intelligence_atlas_baseline.md` — the actual frozen baseline:
  git/migration/contract state, real command output (not prose), the public
  artifact's source/document distribution, and a risk register.
- `tests/fixtures/runtime/source_capability_matrix.json` — the machine-
  checked capability matrix: one row per source returned by
  `src/ops/runtime.py::configured_ingest_source_names()`, built from real
  scraper configuration and the most recent evidence this environment had
  access to. Unmeasured fields are `null`, never inferred.
- `tests/unit/test_source_capability_matrix.py` — locks the matrix to the
  live scraper registry so a source added/removed/renamed in
  `src/pipeline/ingest_core.py::build_scrapers()` without regenerating the
  fixture fails a test instead of silently going stale.

## How to use the baseline in later tasks

1. **Don't hand-edit the matrix fixture.** If the scraper registry changes,
   regenerate it (see the script recipe in
   `benchmarks/intelligence_atlas_baseline.md`) and let
   `test_source_capability_matrix.py` prove it still names exactly the
   configured sources.
2. **Configured and public are different claims.** `configured: true` means
   a source exists in `build_scrapers()`. `current_public_document_count`
   means it actually reached the live public snapshot. At baseline time,
   21 sources were configured and only 3 were represented publicly — see
   the baseline doc for the exact counts. Later tasks that show "coverage"
   on the dashboard must keep these two numbers visibly separate, not
   collapse them into one "supported sources" claim.
3. **A `null` field is a known gap, not a zero.** `last_yield: null` does
   not mean "yielded nothing" — it means this baseline had no ledger access
   to say either way. Treat `null` fields as work items for whichever task
   wires up real ledger queries, not as settled facts.
4. **The risk register in the baseline doc is load-bearing**, not
   decoration — cite it (30-minute pipeline timeout, source drift,
   translation quota, data backlog, migration overlap, cross-repository
   consumer drift) when a later task's design has to choose a tradeoff
   under one of these constraints.
