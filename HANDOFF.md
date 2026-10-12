# OSINT improvements: backend review handoff

Built against `3a9bd6978fb57573ad323b8f6dcb81ccc8492922` on 2026-10-12.
This batch combines five backend/evaluation improvements. It is prepared as a
separate draft PR; no deployment or feature activation is included. The frontend
work has not started and can use the current console on main as its baseline.
See [the frontend integration checkpoint](docs/FRONTEND_INTEGRATION.md) for the
fixed interfaces, unchanged v1 contract, and consumer/activation readiness gates.

## What is usable

1. **Saved extraction evidence reports.** `scripts/m3_checkpoint.py --out`
   saves every prediction, missed/spurious span, score, extractor setting,
   source-fixture hash, and relevant code/configuration fingerprint. Reports
   are immutable: a changed run needs a new output filename. Missing, skipped,
   initialization-failed, and observed inference-failed model runs are distinct.
   The existing 15-document authored fixture still scores 31 true positives,
   1 false positive, and 4 false negatives (F1 0.92537). This is regression
   evidence, not a production accuracy estimate.
2. **Safer entity-resolution evaluation.** Tied scores now enter average
   precision together, so row order cannot improve the result. Train/test
   groups follow connected name endpoints, keeping A–B and B–C together.
   Legacy immutable label files remain readable; missing endpoint metadata
   cannot establish leakage-safe evaluation. Re-export such labels into a new
   snapshot rather than rewriting an old one. Live scorer weights are unchanged.
3. **Pipeline diagnostics and one measured fast path.** Stage, source write,
   commit, and resolver-phase boundaries expose elapsed time and bounded
   counters without logging raw source text or exception bodies in the new
   diagnostics. Empty cannot-link sets bypass pointless pair checks, while
   preserving sorted output and duplicate-ID rejection. For 10,000 IDs the
   isolated benchmark removes 49,995,000 membership probes. It does not skip
   the overall resolver recompute or prove the production timeout is fixed.
4. **Opt-in complete-current metadata archive.** A strict additive archive
   includes every currently processed live document, including undated or
   unassigned-country records and rows beyond the existing 50-article country
   sample. Memberships, current aliases, URLs, schema, content identity, and
   cross-file consistency are validated. The exporter reads one database
   snapshot and stages the archive with the existing rollback-safe bundle.
   It contains metadata only, with snapshot-local entity IDs. “Complete” does
   not mean all historical, unprocessed, or retracted records.
5. **Offline bilingual evidence lookup.** A dependency-free reader accepts a
   bounded caller-supplied snapshot and returns Arabic/Latin name matches with
   exact cited original Arabic spans. It distinguishes original names,
   romanizations, attested aliases, and optional cached translations. Hashes,
   Unicode offsets, references, retractions, ambiguity, and output limits are
   checked. The supplied example is synthetic and has no translations.

The archive and private evidence reader are separate boundaries. Source bodies
and evidence excerpts are not added to the public dashboard contract. Existing
HTML, workflows, migrations, dependency files, consumer contract/lock, and
scorer weights remain unchanged. Worker CORS adds only the exact archive path,
`/countries/_atlas.json`, alongside the existing `/data.json` path.

## Try the private sample

From `source/` in the package, or the repository root after applying the patch:

```sh
python -S -m src.search.evidence_cli \
  --snapshot tests/fixtures/bilingual_evidence.json \
  --query 'Mohammed al-Ahmad' --limit 1

python -S -m src.search.evidence_cli \
  --snapshot tests/fixtures/bilingual_evidence.json \
  --query 'مُحَمَّد الاحمد' --limit 1

python -S -m src.search.evidence_cli \
  --snapshot tests/fixtures/bilingual_evidence.json \
  --query 'muhammad ahmad' --span-characters 25
```

These find snapshot-local entity `1` (`محمد الأحمد`). The first full evidence
window is document `1`, `[0:67]`; its original mention is `[21:36]`. The result
contains the exact Arabic source, its hashes, and extractor/segmenter identity.
A short window is explicitly labelled cropped. Missing translation stays
`translation: null`, not an invented English rendering. The example URLs use
`example.invalid`; they are fixture identifiers, not real news sources.

With the existing pinned requirements installed, save the extraction report:

```sh
python scripts/m3_checkpoint.py --skip-model --out reports/m3-golden.json
```

The separately delivered source package includes an `examples/` directory of
independently checked sample outputs; that directory is not part of this PR.
The commands above reproduce the examples from this checkout. The M3 report
identifies its code by file hashes; a source archive without Git history
truthfully reports an unknown Git revision.

## Archive activation boundary

A configured **disposable test database** can be exported locally with:

```sh
python -m scripts.bake_atlas --out /tmp/osint-archive-candidate/data.json
```

This is a database read, not a fixture-only command. It creates a local
candidate and never deploys or requests a translation. The default main CLI,
workflow, and runtime do not enable it. A later default v1-only bake replaces
the country directory and removes a previously staged archive, so future
activation must be coordinated end-to-end. The existing immutable release
collector can carry and verify the archive, as covered by an integration test.

## Verification

- Full Python suite: **703 passed, 7 skipped, 2 expected failures**.
- Direct Worker CORS suite: **9 passed**; also exercised by the Python suite.
- Dashboard contract generator `--check`: passed; v1 remains byte-for-byte
  unchanged. The new archive schema also matches its strict Pydantic model.
- Original UI/workflow/migration/scorer-file hashes match the base checkout.
- Archive rollback, SQLite concurrent-snapshot isolation, immutable candidate
  round-trip, no-publication events, and narrow CORS are regression-tested.
- Arabic/Latin fixture commands work with `python -S`; every returned source
  and mention slice/hash is verified. No real API, database service, R2, or
  paid translation was used.
- Tested on Python **3.12.14** and Node **24.19.0**, with every version in
  `requirements.txt` matched exactly. No dependency was added.

The seven skips are six PostgreSQL migration tests and the optional real
transformer test. Two known Arabic normalization gaps remain expected failures.
Four existing Python 3.12 SQLite datetime deprecation warnings remain.
PostgreSQL archive isolation, optional ML inference, Python 3.11 CI, portfolio
consumer build, and production behavior were not validated here.

Re-run local checks with no service credentials or `.env` file configured:

```sh
python -m pytest -q -ra
python scripts/generate_dashboard_contract.py --check
node --test tests/atlas-worker.test.mjs
python -m scripts.bench_resolution_partition --sizes 1000 5000 10000 --repeat 3
git diff --check
```

The tests use SQLite, local blobs, and stubbed providers. Do not set
`MIGRATION_TEST_DATABASE_URL` unless it names a disposable PostgreSQL database;
those tests create and remove isolated test databases. Benchmark timings are
machine-specific and are not production forecasts.

## Separate source package and next checkpoints

The earlier standalone archive contains `changes.patch`, `source/`,
`verification.json`, `SHA256SUMS`, and example outputs. Those packaging files
are not repository paths or inputs required by this PR. Its verification record
describes the original 40-file source package; this PR also adds the frontend
integration checkpoint and updates this handoff for repository use.
The standalone patch applies to the exact base above and excludes Git history,
virtual environments, credentials, and runtime caches. Check compatibility
before applying that original patch to another checkout:

```sh
git apply --check /path/to/changes.patch
git apply /path/to/changes.patch
```

Before production use:

1. Build against the fixed interfaces from the current console baseline, then
   run the actual consumer checks listed in `docs/FRONTEND_INTEGRATION.md`.
   Reconcile parallel archive implementations only if deliberately reusing them.
2. Wire the private reader to a coherent authorized database/blob snapshot
   and actual alias/cache provenance. Its current inputs are detached records,
   not proof of a live database generation or a provider's authorship.
3. Settle excerpt permissions and private/public access, then integrate the
   reader with a consumer/UI. No Evidence Lab or dashboard wiring is included.
4. Activate the archive through its runtime/workflow and consumer together;
   test PostgreSQL isolation and the pinned portfolio consumer.
5. Use genuine held-out labels and an authorized monitored pipeline run before
   claiming better model quality or resolved production latency.

Design details and Python explanations are in ADRs 0033–0037,
`docs/M3_EVALUATION_REPORTS.md`, `docs/M4_LABELING_AND_EVALUATION.md`,
`docs/BILINGUAL_EVIDENCE_HANDOFF.md`, `contracts/atlas/README.md`, and
`benchmarks/results.md`.
