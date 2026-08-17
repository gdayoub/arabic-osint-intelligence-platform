# ADR 0003: Object/link types are validated strings + JSONB, not SQL enums or per-type tables

## Context

P3 requires object types (`person`, `organization`, `location`, ...) and link
types to live in `config/ontology.yaml`, not be hardcoded in Python classes or
SQL schema. `entities.object_type` and `entities.properties` need to store
this without the database schema itself encoding the type list.

## Options considered

1. **SQL enum + one table per object type.** `CREATE TYPE object_type AS ENUM
   (...)`, and e.g. a `person_entities` table with real `date_of_birth`
   column, `organization_entities` with a real `org_type` column, etc.
2. **`object_type VARCHAR` + `properties JSONB`, validated in application
   code against the loaded `Ontology`.** One `entities` table for every
   object type; the ontology file, not the schema, defines what properties
   are legal for a given type.

## Decision

Option 2. `src/core/ontology.py` loads `config/ontology.yaml` and is the only
thing that knows what `person` or `member_of` mean; `EntityORM.object_type`
is just a string, `EntityORM.properties` is just JSON, and
`src/store/provenance.py`'s `create_entity`/`link_entities` call
`ontology.is_valid_object_type(...)` / `ontology.is_valid_link_type(...)`
before writing.

## Consequences

- Adding a new object type (e.g. `event` in M7, or a new language's types)
  is a YAML edit, not a migration. This is the whole point of P3 — verified
  directly by this design, since the M7 `Event` type will be addable without
  touching `src/store/orm.py` or running `ALTER TYPE`.
- The database cannot enforce property shape (a `person` entity could have
  `properties = {"anything": "goes"}` and Postgres would accept it — only
  `create_entity`'s application-level check catches a mismatch, and even
  that check doesn't currently validate individual property types/required
  flags, just the `object_type` string itself). This is a real gap, tracked
  as decision #1 in the M1 report — worth a property-shape validator in a
  later milestone if bad data shows up in practice.
- No relational queries like "all persons born before 1980" without a JSONB
  path expression (`properties->>'date_of_birth'`), which is slower and
  clunkier than a real column. If a specific property turns out to need
  real indexing (most likely `location.geom` for PostGIS in M7), the plan is
  to special-case that one property into a real column rather than abandon
  JSONB for everything — see the M1 report's flagged risk #1.
