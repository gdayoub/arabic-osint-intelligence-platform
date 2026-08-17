# ADR 0001: Separate core dataclasses from the SQLAlchemy ORM

## Context

M1 needs a `Document`/`Mention`/`Entity`/`Link`/`Fact`/`Provenance` data model.
The repo layout in the brief puts dataclasses in `src/core/` ("no domain
logic") and persistence in `src/store/`. The obvious shortcut is to skip the
dataclasses and just use SQLAlchemy ORM classes everywhere, like the existing
`src/database/models.py` does for `raw_articles`/`processed_articles`.

## Options considered

1. **One set of classes**: SQLAlchemy ORM models used directly throughout the
   codebase (what the existing article pipeline does).
2. **Two sets of classes**: plain dataclasses in `src/core/` representing the
   domain, SQLAlchemy ORM classes in `src/store/` representing storage, with
   `src/store/provenance.py` converting between them at the boundary.

## Decision

Option 2. `src/core/models.py` has zero SQLAlchemy imports. `src/store/orm.py`
has the SQLAlchemy mapping. `src/store/provenance.py` is the only place a
`MentionORM` row turns into (or comes from) a `core.models.Mention`.

## Consequences

- Extra boilerplate: every repository function ends with a `_to_entity(row)`
  style conversion. This is the cost, and it's paid on every function.
- In exchange: `src/core/` — where object types, offsets, and provenance
  rules (P1-P3) actually live conceptually — can be unit-tested with zero
  database, zero Docker, zero network. A test like "offsets must be valid"
  (P2) doesn't need a running Postgres to fail correctly.
- If M4's entity resolution needs a different storage engine for one piece
  (e.g. a vector index for embeddings, not a relational table), only
  `src/store/` changes. `src/core/` and anything that imports it stays
  untouched — this is the same reasoning P3 uses for `LanguageAdapter`,
  applied to storage instead of language.
- Downside if this is wrong: if the two models drift (a field added to the
  dataclass but forgotten in the ORM mapping, or vice versa), nothing catches
  it except a runtime `AttributeError`. No test currently asserts the two
  stay in sync field-for-field. Worth revisiting if that happens in practice.
