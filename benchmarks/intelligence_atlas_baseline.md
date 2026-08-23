# Intelligence Atlas baseline (Task 1)

**Frozen:** 2026-08-23. This is the "before" snapshot Tranche A (see
`docs/execution/intelligence-atlas/tranche-a.md`) diffs against. Every
number below is either a named command's real output or a value read
directly from a named file/URL — no prose summaries stand in for a
measurement.

**Environment note:** this worktree has no Python 3.11 interpreter (a known,
ledgered gap — see `.superpowers/sdd/2026-08-22-intelligence-atlas-implementation/progress.md`).
All commands below ran under `.venv312/bin/python` (Python 3.12.14, all of
`requirements.txt` installed). CI's own 3.11 pin remains the authoritative
compatibility gate; local 3.12 success here is not proof of 3.11 behavior.

## Identity

| field | value |
|---|---|
| git commit | `64b2089439512a424c12d921e28528c21b2d289f` (`git rev-parse HEAD`, branch `worktree-intelligence-atlas`) |
| Alembic head | `0005_stable_entity_generations` (`migrations/versions/0005_stable_entity_generations.py`, the highest-numbered file in `migrations/versions/`) |
| dashboard contract version | `1.0.0`, schema_version `1` |
| dashboard contract lock (`contracts/dashboard/contract.lock.json`) | `bundle_sha256=df38f614b6bcd210e2ec061031a61245dba361e367a965123c7b1221bb3d7128`, `manifest_sha256=37fb81ff81411819b05bef46571f0ce59af7febb0f9660f96f16950bb7a43ead` |
| portfolio consumer lock (`contracts/dashboard/consumers/george-portfolio.lock.json`) | pins consumer commit `203a63b38f53bccfe28e0e53e02e4d0a2c603c4c` of `gdayoub/george-portfolio`, against producer contract commit `d650feb1446cc6154d8a348246d01c02443f23b3`, `bundle_sha256=df38f614b6bcd210e2ec061031a61245dba361e367a965123c7b1221bb3d7128` — **matches the producer contract lock's `bundle_sha256` exactly**, so the two locks agree with each other even though the producer commit that generated the currently-committed lock (`d650feb1...`) predates this worktree's HEAD. |
| Cloudflare release (proxy identity; no Cloudflare API token available in this environment) | live `data.json` at `https://arabic-osint-dashboard.georgedayoub500.workers.dev/data.json` returns `generated_at=2026-08-22T04:39:23.586295+00:00`, `etag=W/"658e05560cacc050ab10994172c32bb5"`, `cf-ray=a2f84d2ddf204693-EWR`, `server: cloudflare`, HTTP 200. `schema_version=1` matches the contract's schema_version. |
| portfolio URL response | `GET https://george-dayoub-portfolio.vercel.app/osint-dashboard.html` -> HTTP `200`, `content-type: text/html; charset=utf-8`, `server: Vercel` |

## Clean baseline commands — real output

### `python -m pytest tests/unit -q`

```
$ .venv312/bin/python -m pytest tests/unit -q
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 45%]
.........xx............................................................. [ 60%]
s....................................................................... [ 75%]
........................................................................ [ 91%]
..........................................                               [100%]
471 passed, 1 skipped, 2 xfailed, 4 warnings in 5.47s
```

Matches `.superpowers/sdd/2026-08-22-intelligence-atlas-implementation/progress.md`'s
recorded pre-branch baseline (471 passed, 1 skipped, 2 xfailed) — no drift
between branch creation and this task.

### `python scripts/generate_dashboard_contract.py --check`

```
$ .venv312/bin/python scripts/generate_dashboard_contract.py --check
dashboard contract 1.0.0 is current
```

Exit code 0. The committed `contracts/dashboard/1.0.0/` bundle and
`contract.lock.json` are byte-identical to what the generator produces from
`src/contracts/dashboard.py` right now.

### `python scripts/check_dashboard_consumer.py`

The task brief's exact command (no arguments) fails at argument parsing,
because `--consumer-root` is required and this worktree does not have a
checked-out copy of `gdayoub/george-portfolio` to point it at:

```
$ .venv312/bin/python scripts/check_dashboard_consumer.py
usage: check_dashboard_consumer.py [-h] --consumer-root CONSUMER_ROOT
                                   [--checkpoint CHECKPOINT]
check_dashboard_consumer.py: error: the following arguments are required: --consumer-root
```

Exit code 2. This is a real, reproducible gap in this environment, not a
consumer-compatibility failure — recorded honestly rather than papered over.
As a substitute, pointing `--consumer-root` at this same repository (which
is not the portfolio repo, so a mismatch is expected) confirms the script
runs and produces a real diagnostic rather than crashing silently:

```
$ .venv312/bin/python scripts/check_dashboard_consumer.py --consumer-root .
consumer git revision mismatch: expected '203a63b38f53bccfe28e0e53e02e4d0a2c603c4c', found '64b2089439512a424c12d921e28528c21b2d289f'
missing consumer contract lock: /Users/georgedayoub/.../contracts/dashboard/consumer.lock.json
```

Exit code 1 (expected — the OSINT repo is not the portfolio repo). The
producer-side consistency check that this script *would* run against a real
portfolio checkout — comparing `contracts/dashboard/consumers/george-portfolio.lock.json`'s
`bundle_sha256` against `contracts/dashboard/contract.lock.json`'s
`bundle_sha256` — was done by direct file comparison above (Identity table):
both equal `df38f614b6bcd210e2ec061031a61245dba361e367a965123c7b1221bb3d7128`.
That is the "portfolio consumer lock matches the backend lock" acceptance
item; a from-scratch clone of the portfolio repo to fully execute the script
was out of scope for a docs/fixtures-only task.

### `python scripts/smoke_public_urls.py`

```
$ .venv312/bin/python scripts/smoke_public_urls.py
public smoke passed: Cloudflare snapshot and exact Vercel routes are compatible
```

Exit code 0. Confirms in one run: the Cloudflare `data.json` is HTTP 200,
allows the portfolio origin via CORS, validates against
`DashboardSnapshot`; the portfolio's `/osint-dashboard.html` is HTTP 200
HTML that still points at the pinned Cloudflare URL; and the portfolio's
`/api/osint` route is HTTP 200 with the expected `stats`/`recent`/`topics`
shape.

## Public artifact: source and document distribution

Queried live from `https://arabic-osint-dashboard.georgedayoub500.workers.dev/data.json`
(with the portfolio `Origin` header, matching how the real consumer calls
it) at snapshot time `generated_at=2026-08-22T04:39:23.586295+00:00`
(roughly 27 hours before this baseline was frozen).

| field | value |
|---|---|
| `stats.total_raw` | 712 |
| `stats.total_processed` | 712 |
| distinct sources represented in `stats.sources` | **3 of 21 configured** |

| source | public document count |
|---|---|
| AlJazeeraArabic | 422 |
| BBCArabic | 153 |
| CNNArabic | 137 |

**The other 18 configured sources (`AnNahar`, `Youm7`, `LibyaAlAhrar`,
`AlKhaleej`, `AlMasryAlYoum`, `SANA`, `AsharqAlAwsat`, `AlMada`,
`AlChourouk`, `AmmonNews`, `Hespress`, `Akhbarona`, `AlRai`,
`SudanTribune`, `AlMasdarOnline`, `AkhbarAlKhaleej`, `Wafa`,
`AlWatanVoice`) have zero documents in the current public snapshot.** This
is the central finding of this baseline: `configured` (present in
`configured_ingest_source_names()`) and "reached the public artifact" are
very different claims, and Tasks 2-6 exist specifically to close that gap
honestly (budget-aware scraping, checkpoints, a proven full-job time budget,
then a real 21-source production checkpoint) rather than by simply widening
what counts as "supported."

Full detail is machine-readable in
`tests/fixtures/runtime/source_capability_matrix.json` (locked to the live
registry by `tests/unit/test_source_capability_matrix.py`).

## Capability matrix — how it was built

`tests/fixtures/runtime/source_capability_matrix.json` has one row per
source name returned by `src.ops.runtime.configured_ingest_source_names()`
(21 rows; this function reads `src/pipeline/ingest_core.py::build_scrapers()`,
so the matrix cannot silently drift from the real scraper registry). Each
row records:

- `source_name`, `configured` — always known, straight from the registry.
- `current_public_document_count` — always known, straight from the live
  public snapshot above (0 for a configured source that isn't represented).
- `live_attempted`, `body_paragraphs_preserved`, `published_date_quality`,
  `robots_policy_checked`, `state_media_metadata`,
  `last_attempt_duration_seconds`, `last_yield`, `last_failure_reason` —
  **all `null` for every source.** This environment has no Neon/pipeline
  ledger database credentials, so there is no way to query real per-source
  attempt telemetry (`PipelineEventORM` rows via `src/ops/ledger.py`) from
  here. Per the brief ("use null for unavailable measurements; never infer
  success from configuration"), leaving these `null` is the honest choice —
  a source with `current_public_document_count > 0` obviously *was*
  attempted successfully at some point, but this baseline does not encode
  that inference into `live_attempted` because it cannot cite the actual
  ledger row that proves it.

Regenerate the fixture (never hand-edit it) with:

```bash
.venv312/bin/python - <<'PY'
import json, sys
sys.path.insert(0, ".")
from src.ops.runtime import configured_ingest_source_names
# ... rebuild REPRESENTED_IN_PUBLIC_SNAPSHOT from a fresh data.json fetch,
# keep every other field null unless real ledger access exists ...
PY
```

and re-run `python -m pytest tests/unit/test_source_capability_matrix.py -q`.

## Risk register

Short and specific — these are the constraints later Tranche A tasks (2-6)
have to design around, not a general risk list.

| risk | what it is | why it matters here |
|---|---|---|
| **30-minute pipeline timeout** | The scheduled GitHub Actions workflow's hard job timeout is 30 minutes end to end (checkout, deps, tests, all pipeline stages, bake, deploy). Task 5 budgets a 19-minute data-stage envelope and a 25-minute full-job target *inside* that hard ceiling, leaving only ~5 minutes slack. | A single slow or hanging source, or a slow dependency install, can eat the whole run before any data stage starts. Task 4's per-source/global `IngestionBudget` exists specifically to keep this from being all-or-nothing. |
| **Source drift** | A scraper that works today can break silently tomorrow: a CSS selector shifts, a listing page changes markup, a source starts serving a bot-management challenge (Al Arabiya was permanently removed for exactly this — see commit `e7a51a9`). | This baseline shows only 3/21 sources actually reaching the public snapshot. Some of that gap may already be silent drift on sources added in this session but never observed producing output post-deploy — `live_attempted: null` in the matrix is this baseline honestly admitting it cannot currently tell "never scheduled a run since being added" from "attempted and silently failing." |
| **Translation quota** | M5's bilingual layer (title translation live; entity-name/evidence-sentence translation is Task 3's scope) depends on a paid or rate-limited translation provider (DeepL, per `AGENTS.md`). | Backfill runs (Task 5, 60-day lookback) and any future scale-up of translated surfaces multiply request volume; a quota exhaustion mid-run must degrade (skip translation) rather than fail the whole pipeline — `run_core_pipeline`'s existing `translate` stage already catches and logs rather than raising (see `src/ops/runtime.py::build_core_pipeline_stages`, the `run_translation` closure), but quota *budgeting* ahead of a large backfill is not yet designed. |
| **Data backlog** | Sources not yet caught up to their full available history, or paused/degraded sources accumulating unprocessed articles. | Task 4 introduces `backlog_estimate` as an explicit, visible metric ("a metric, not hidden behind a green source status" per the plan's Task 4 acceptance criteria) specifically because this baseline has no way to distinguish "source has no backlog" from "source has never been checkpointed." |
| **Migration overlap** | Tasks 3 and 4 both add Alembic migrations (`0006` bilingual, `0007` source registry) in immediate succession, and the plan requires them to land as a single strict linear chain (0006->0007->...->0016 across eleven of the plan's 48 tasks). | Alembic heads must never fork. This baseline records the current head as `0005` precisely so Task 3 has an unambiguous "before" to build `0006` on top of, and so a reviewer can catch a fork immediately rather than discovering it downstream. |
| **Cross-repository consumer drift** | The dashboard contract (`contracts/dashboard/`) is consumed by a separate repository (`gdayoub/george-portfolio`) pinned via `contracts/dashboard/consumers/george-portfolio.lock.json`. A schema change on this side that isn't matched by a portfolio-side update breaks the live public page. | `scripts/check_dashboard_consumer.py` is the guard, but this baseline could only run it in degraded form (see above) because no portfolio checkout exists in this environment. Any Tranche A/G/H task that changes `src/contracts/dashboard.py` must re-run the full two-repo check with a real portfolio checkout before it can claim the consumer lock still matches. |

## Acceptance checklist (Task 1)

- [x] The baseline is reproducible from named commands — see "Clean baseline
      commands" above; every result is a pasted command and its real output.
- [x] All 21 configured sources appear exactly once —
      `tests/unit/test_source_capability_matrix.py::test_matrix_names_exactly_the_configured_sources`
      asserts this against the live `configured_ingest_source_names()`
      registry, currently 21 sources.
- [x] Public and configured coverage are shown separately — see "Public
      artifact: source and document distribution" (3 of 21 represented
      publicly) and the matrix's `configured` vs
      `current_public_document_count` fields, independently asserted by
      `test_public_document_counts_match_the_recorded_public_snapshot`.
- [x] The portfolio URL responds and its consumer lock matches the backend
      lock — HTTP 200 confirmed both via `smoke_public_urls.py` and a
      direct request (Identity table above); `bundle_sha256` in
      `contracts/dashboard/consumers/george-portfolio.lock.json` matches
      `contracts/dashboard/contract.lock.json` byte-for-byte
      (`df38f614b6bcd210e2ec061031a61245dba361e367a965123c7b1221bb3d7128`).
