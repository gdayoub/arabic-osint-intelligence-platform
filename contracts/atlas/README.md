# Complete-current metadata archive (opt-in, v1)

The authoritative producer contract is `src/contracts/atlas.py`. Its JSON Schema
and synthetic fixture are in `1.0.0/`. This is a new additive artifact; the
existing dashboard 1.0.0 contract and consumer lock are unchanged.

## Exact scope

`complete: true` with `scope: current_processed_live_documents` means one row
for every non-retracted document whose latest topic and escalation facts belong
to the configured classifier version, in one database read snapshot. It includes
all those rows without a recent/country limit, even without country, publication
date, or entity memberships. It excludes unprocessed documents, retracted
records, and historical processing generations. It is **not an all-history
archive** and contains no document bodies or evidence sentences.

- `classification` identifies the current processor name and version.
- `generated_at` matches the accompanying v1 dashboard/country bundle.
- `snapshot_id` is SHA-256 over canonical JSON of the complete archive except
  that field. It detects mixed or changed archive content, not historical
  provenance or durable identity. It is not a digest of the pretty-printed file.
- Article `id` is the existing document row ID rendered as text.
- Entity `id` is the existing resolver row ID rendered as text. The explicit
  `entity_id_scope: snapshot_local` means it must not be saved as a durable
  profile/bookmark identifier across resolver generations.
- `published_date` and `collected_at` retain their separate meanings. Neither
  is a classification timestamp. The legacy v1 `processed_at` field remains
  unchanged and is compared to archive collection time only for compatibility.
- `title_en` and `name_en` contain existing cached machine translations or null.
  No translation call is made. `translation_scope` labels this limitation.
- Entity names, current name surface forms, and deterministic romanizations are
  search metadata. No raw text, span offsets, blob keys, hashes of private source
  bodies, evidence sentences, or new private bilingual artifacts are exported.

## Validation and publication boundary

All models forbid unknown fields and unsafe public URLs. Identities and
memberships must be unique; references must resolve; every exported entity must
have an included current article. The producer additionally compares the exact
set of document IDs against its current-processing selection. One live mention
cannot silently belong to two different live resolver entities, and membership
object types must agree. Surface forms come only from included current live
mentions, never the entity's historical properties bag.

Cross-file validation checks timestamp, processed count, topic/escalation/source
aggregates, country membership, and the actual metadata in every country sample
and completed recent row. Counts alone cannot validate a sample. Dashboard v1
has no document IDs, so only its observable metadata can be compared; exact ID
validation remains the producer's responsibility. Unprocessed recent rows are
valid v1 content and are intentionally outside archive scope.

The opt-in exporter reads all three payloads in a dedicated repeatable-read
PostgreSQL transaction (read-only), or an explicit SQLite transaction. Every
candidate is validated before staging. `countries/_atlas.json` participates in
the existing country-directory swap and rollback together with v1 files.
Local multi-file staging is a rollback-safe candidate build, not a claim of
atomic network publication; deployment still belongs to the immutable-release
promotion machinery already on main.

## Run locally

With the repository's pinned requirements installed:

```
python -m scripts.bake_atlas --out /tmp/osint-archive-candidate/data.json
```

This command reads the configured database and writes a local candidate only.
Use a fixture/test database unless you have authorized access to the intended
database. It never deploys, mutates stored facts, fetches bodies, or buys a
translation. The existing main CLI, scheduled workflow, and runtime stage do
not opt in automatically. A later separately reviewed activation must pass
`include_atlas=True`; the existing runtime artifact collector already handles
`countries/*.json`.

Offline boundary tests (Pydantic only):

```
python -m unittest discover -s tests/unit -p 'test_atlas_contract.py' -v
```

ORM and local publication regressions:

```
python -m pytest tests/unit/test_atlas.py tests/unit/test_atlas_contract.py tests/unit/test_bake.py tests/unit/test_dashboard_contract.py -q
```

The JSON Schema is generated with the repo pin `pydantic==2.12.5`; its check
intentionally detects drift. No schema migration or new dependency is needed.
