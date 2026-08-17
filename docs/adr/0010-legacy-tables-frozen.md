# ADR 0010: Legacy `raw_articles`/`processed_articles` frozen; `documents` is the system of record

## Context

After M1.5, two schemas coexist in the same database: the original
`raw_articles`/`processed_articles` (`src/database/models.py`) and the new
`documents`/`mentions`/`entities`/`facts` (`src/store/orm.py`). Something
has to be authoritative for what the dashboard shows and what new ingestion
writes to.

## Decision

`documents` (+ `facts`) is the system of record going forward.
`raw_articles`/`processed_articles` are **frozen**: nothing writes to them
anymore (`src/pipeline/ingest_pipeline.py` and `process_pipeline.py` still
exist and still work if invoked directly, but nothing in
`.github/workflows/pipeline.yml` calls them). They are not dropped, migrated,
or backfilled into the new schema — see the decision to abandon the Railway
backfill (cost of temporarily un-pausing Railway wasn't worth preserving a
corpus that would be stale within weeks anyway).

`src/api/static/dashboard.html` reads only the baked `data.json`
(`scripts/bake_dashboard_data.py`), which reads only `documents`/`facts`.
The Streamlit app (`src/dashboard/app.py`) and FastAPI service
(`src/api/main.py`), which still read the legacy tables, are not part of the
hosting plan (ADR 0008) — they're left in the repo, undeployed, not
actively maintained.

## Consequences

- The legacy tables' data (if any exists locally in a dev database) is
  effectively inert history — visible via direct SQL if someone wants it,
  invisible to the dashboard and the pipeline.
- Anyone extending ingestion (a new source, a new field) should extend
  `src/pipeline/ingest_core.py`, not `ingest_pipeline.py` — the latter is a
  dead end.
- `src/database/db.py` and `src/store/database.py` remain two separate
  engine/session modules pointing at the same `DATABASE_URL`. This is
  duplication worth resolving eventually (collapse to one engine module
  once the legacy schema's code paths are actually deleted, not just
  unused), but doing it now would mean touching code that isn't broken to
  fix a cosmetic overlap — deferred.
