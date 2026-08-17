# ADR 0002: Provenance is enforced at the Python API boundary, not by a DB trigger

## Context

P1 requires that it be *impossible* to write a fact without provenance — "no
bypass." The strongest version of that guarantee is a database-level
constraint: something Postgres itself refuses to violate, regardless of what
application code does.

## Options considered

1. **Postgres deferred constraint trigger.** An `AFTER INSERT` trigger on
   `mentions`/`entities`/`links`/`facts`, declared `DEFERRABLE INITIALLY
   DEFERRED`, checked at `COMMIT` time, that fails the transaction if no
   matching `provenance` row exists for the new row's `(table, id)`. This is
   real, database-enforced "no bypass" — even a raw `INSERT` from `psql`
   would fail.
2. **Python API boundary.** `src/store/provenance.py` exposes only functions
   (`create_mention`, `create_entity`, `link_entities`, `record_fact`) that
   write both the fact row and its provenance row in the same session, and
   nothing else in the codebase is allowed to import `MentionORM` etc.
   directly to insert. Enforced by code review and a test, not the database.

## Decision

Option 2, for M1. Option 1 is real and I considered it seriously — it's not
a strawman, it's what "impossible" literally requires — but it adds a
`DEFERRABLE` constraint trigger, which is genuinely more moving parts than
this milestone needs to prove the concept, and the brief's own guidance
(§0.4) is to prefer boring code and add complexity when the pain is felt, not
speculatively.

## Consequences

- The guarantee right now is: "nothing in this codebase currently bypasses
  provenance," not "nothing ever could." A future contributor (or future me,
  six months from now) could add a new insert path and forget the
  provenance row, and Postgres would not stop them — only a test would.
- `tests/unit/test_provenance.py` exists specifically to catch that class of
  regression: it asserts every mention/entity/link created through the
  sanctioned functions has a matching provenance row.
- **What would change my mind:** if M8 or M11 (access control, which also
  wants strong DB-level guarantees via row-level security) shows that
  "enforced in application code" isn't good enough in practice — e.g. a
  real bug slips through because someone added a direct insert — that's the
  signal to build the deferred constraint trigger from option 1. At that
  point there's a concrete failure to point to, not just a hypothetical one.
