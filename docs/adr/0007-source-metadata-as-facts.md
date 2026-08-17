# ADR 0007: Article metadata (title, author, tags...) lives in `facts`, not `documents` columns

## Context

Legacy `raw_articles` (`src/database/models.py`) has `title`, `subtitle`,
`author`, `tags`, `source_section` as real columns. The new `documents`
table (M1) intentionally does not — `documents` is meant to be domain- and
source-agnostic (P3), and "author" and "tags" are concepts specific to news
articles. A Telegram message or a court filing wouldn't have them in the
same shape.

## Options considered

1. **Add the columns to `documents` anyway.** Simplest, matches the legacy
   shape, but bakes news-specific structure into the core schema — a direct
   P3 violation, and exactly the kind of hardcoding the brief calls out.
2. **A generic `documents.metadata` JSONB bag**, arbitrary key-value pairs
   with no schema. Rejected: it would need to be `UPDATE`d in place to
   correct a field, which violates P5 (append-only, supersede don't
   mutate), and nothing records provenance for values changed inside it,
   violating P1.
3. **A dedicated `document_attributes` table.** Considered and rejected as
   premature — it's a new table whose only job is what `facts` already
   does. Building it now is the kind of speculative abstraction §0.4 of the
   brief warns against.
4. **`facts` rows, `subject_table="documents"` — chosen.** `FactORM`
   already exists (M1) as a generic, append-only, provenanced key/value
   store built for exactly this. `record_document_fact()` in
   `src/store/provenance.py` validates `fact_type` against a new
   `document_attributes` section in `config/ontology.yaml` before writing,
   so P3 holds — a Telegram adapter later declares its own attribute list
   in YAML, touching no Python or SQL.

## Decision

Option 4.

## Consequences

- **Row-count cost, stated honestly:** one fact row per non-empty attribute
  is roughly a 5x row multiplier on `facts` relative to `documents` — 5,000
  documents becomes on the order of 25,000 fact rows if all five attributes
  are populated. At Neon's free tier this is a few MB, not a real cost, but
  it is a real number and worth knowing before assuming "facts are free."
- Correcting a value (e.g. a re-scrape finds a better title) means a new
  fact row with `supersedes_id` pointing at the old one, never an `UPDATE`.
  `record_document_fact()` takes an optional `supersedes` parameter for
  exactly this, tested in `tests/unit/test_provenance.py`.
- `config/ontology.yaml`'s `document_attributes` section is an extension the
  original brief didn't specify (it only describes `object_types` and
  `link_types`). It follows the same declarative shape and the same
  `Ontology.is_valid_*` validation pattern already established for object
  and link types, so it's a consistent extension rather than a new
  convention.
- Aggregating over `facts` (e.g. "count documents by topic") means grouping
  on a JSON field rather than a plain column — `payload->>'value'` on
  Postgres, `json_extract` on SQLite. Slightly more code at query time than
  a real column would need; deferred until a real aggregation need shows
  whether that cost matters (Stage 3b's `process_core.py` is the first
  concrete consumer).
