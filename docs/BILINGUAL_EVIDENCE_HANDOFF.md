# Offline bilingual evidence checkpoint

## What is usable now

`search_evidence(corpus, query)` is a reusable, private read function. The
local CLI accepts a small JSON snapshot and prints typed entity matches plus
bounded, exact Arabic source spans and their provenance. It runs without
installed dependencies (`python -S` works), credentials, network, or a database.

The supplied example is **entirely synthetic**, including its people, source,
URLs, extractor, and alias attestation. It has two short Arabic documents,
two entities, and four mentions. It includes diacritics, emoji, a paragraph
break, and repeated entity evidence. It contains no cached translation and
must not be described as a production export or real news evidence.

## Run the sample

From the repository root:

```bash
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

All three find snapshot-local entity `1`, canonical name `محمد الأحمد`.
The first matches the supplied fixture-attested `verified_alias`, the second
the normalized `original`, and the third a deterministic `romanization`.

The uncropped first evidence is:

> في تقرير تجريبي، قال مُحَمَّد الأحمد إن المكتبة ستفتح أبوابها غداً.

Its document is `1`, source is `synthetic-news-fixture`, and exact source
window is `[0:67]`. The mention is `[21:36]`. The CLI prints actual SHA-256
digests, original code-point length, and extractor/segmenter identity; the
caller can verify every slice directly against the input document.

For each result, inspect:

- `names` and `matched_names`: original, romanized, and attested aliases stay
  separate. A romanized spelling is never labelled a translation.
- `evidence[].text`, `start`, `end`, `span_sha256`, `document_text_sha256`:
  exact source proof. `content_hash` separately preserves ingest metadata.
- `span_kind`, `truncated`, `sentence_start`, `sentence_end`: a 25-character
  crop is explicitly a source window, not a claimed whole sentence.
- `translation_status`: the shipped fixture reports
  `unavailable_for_exact_window`, with `translation=null`.
- `evidence_coverage`: extra windows, oversized mentions, and missing bounded
  live evidence are visible rather than silently hidden.
- Top-level `data_kind`, `scope`, `entity_id_scope`, `access_scope`, and reader
  version: synthetic/offline/private/snapshot-local scope is explicit.

Malformed inputs exit with status `2`, an error on stderr, and no partial
JSON result. The CLI only reads the specified local path and prints to stdout.
Redirect output yourself only to an appropriate private destination.

## Reusable Python interface

```python
from pathlib import Path
from src.search.evidence import search_evidence
from src.search.evidence_cli import load_snapshot

corpus = load_snapshot(Path("tests/fixtures/bilingual_evidence.json"))
result = search_evidence(corpus, "محمد", evidence_per_entity=2, span_characters=180)
```

For a future caller that already has authorized data in memory, construct an
`EvidenceCorpus` directly. It wraps the existing `Document`, `Entity`,
`Mention`, and `ExtractorVersion` records. `SourceDocument` requires the
expected text SHA-256 and code-point length; `SearchEntity` adds the known
source language; `EntityMention` mirrors existing links. No storage write or
new identity is created. The function takes an `AdapterRegistry` if additional
languages are configured.

Optional `VerifiedAlias` records require an entity-linked mention, verifier,
and verification reference. This validates the supplied attestation's shape
and evidence link, not whether the named verifier really approved it.

Optional `CachedEvidenceTranslation` records are detached reads of an actual
artifact and its exact source use. They require full document and span hashes,
span offsets, source/target language, artifact reference, provider/version,
settings hash, output, and explicit origin. One selected artifact per
span/target avoids silently choosing between providers. A stale span or
contradictory artifact fails the entire request. Empty cached results are
distinct from misses. Synthetic rows are labelled `fixture_only`, never real
machine output. No translation is generated, retrieved, paid for, or saved.

## Verification

Run:

```bash
python -m pytest -q tests/unit/test_bilingual_evidence.py \
  tests/unit/test_entity_search.py tests/unit/test_segmentation.py
python -m pytest -q tests/unit
git diff --check
```

The local verification at this checkpoint used Python 3.12.14 and the shared
test environment. Focused tests: **139 passed**. Full unit suite:
**589 passed, 1 skipped, 2 expected failures**. The complete repository suite
also passed: **594 passed, 7 skipped, 2 expected failures**, with
`MIGRATION_TEST_DATABASE_URL` and `DATABASE_URL` unset. The six PostgreSQL
migration checks were skipped because no disposable PostgreSQL server was
used. The skipped transformer test and pre-existing expected failures are not
newly validated behavior. Existing SQLite tests emit four Python datetime
deprecation warnings. `compileall` and `git diff --check` also passed.

Coverage includes both query scripts, typed names, exact diacritics/emoji
offsets, source/hash/length proof, cropping at document edges, cross-sentence
mentions, duplicate-window suppression, deterministic ranking, caps and
coverage signals, retracted entities/documents/mentions/alias witnesses,
English adapter behavior, source URL safety, stale/ambiguous/conflicting
translation provenance, empty/missing/oversized caches, invalid IDs/links,
alias attestations, JSON duplicate keys and input bounds, and a real
standard-library-only CLI subprocess.

## Intentionally unfinished production work

This is not connected to the deployed dashboard, the separate unpublished
Evidence Lab, any API, or a production database. No dashboard/evidence
deployment, excerpt publication, schema migration, or stable-identity
activation is included.

Before production use, an explicit next checkpoint must resolve:

1. A read-only exporter/adapter with coherent snapshot/generation selection,
   real source blob loading, access controls, and missing/corrupt blob handling.
   This reader validates loaded bytes but cannot certify a live transaction.
2. Actual verified-alias attestations and cache artifact/use provenance.
   Legacy title translations and unverified surface forms cannot be substituted.
3. Public excerpt rights, per-source budgets, retention, and authorization.
   Local output caps are not a publication policy.
4. Consumer/UI integration with original and derived text clearly separated,
   correct escaping, and the private/public boundary preserved.
5. Versioning the inherited romanizer if its output becomes persisted history.
   Its version is honestly unknown here; the implementation symbol and reader
   version are recorded, while `producer_version` remains null.

No M5 milestone completion or real-corpus accuracy claim is implied by these
fixture tests. The narrow useful result is a tested, reusable local query and
evidence interface that those later integrations can call.
