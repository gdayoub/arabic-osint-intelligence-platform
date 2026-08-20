# ADR 0015: Per-document savepoints for derived generation replacement

## Context

The processing and mention-extraction jobs handle hundreds of documents in one
SQLAlchemy session. They catch an exception for one document so the rest of the
batch can continue, but they previously made several writes before the point
that marks a document complete.

That combination was unsafe. Catching the Python exception did not undo the
already-flushed rows. A classifier could leave country and escalation facts
without a topic completion fact. An extractor could leave the first few
mentions without its completion marker. The next retry would add more rows to
the same outer transaction. On an extractor-version bump, the pipeline also
wrote a replacement mention set without retiring the prior version, so both
generations remained live and downstream counts could double.

A replacement also has a less obvious absence case. If the old classifier
found a country and the new classifier finds none, omitting the new country row
allows the old value to remain the latest visible leaf. “No country detected”
is itself the current classifier result and must replace the old value.

## Options considered

1. **Keep the completion marker last and rely on retries.** This detects an
   incomplete document, but it does not remove partial facts or mentions that
   were already flushed. A retry can compound the bad state.
2. **Commit or roll back the whole batch.** This is atomic, but one malformed
   document would discard successful work for every other document and make a
   recurring bad input block the pipeline indefinitely.
3. **Commit each document separately.** This isolates failures, but adds a
   network commit round trip per document against Neon and gives up the useful
   outer batch transaction.
4. **Use one nested transaction/savepoint per document — chosen.** PostgreSQL,
   SQLite, and SQLAlchemy already support this. It adds no dependency and lets
   one document roll back without discarding prior successful documents in the
   batch.

## Decision

`process_one_document()` and `extract_one_document()` each own a
`session.begin_nested()` context. In SQLAlchemy this context manager creates a
database savepoint. Leaving it normally releases the savepoint; raising from
it rolls back every insert and update performed since that savepoint while the
outer session stays usable.

Each replacement follows the same sequence inside its savepoint:

1. Read and retain the currently live rows owned by that extractor name and
   pipeline stage.
2. Write every replacement row and its provenance.
3. Write the completion fact last, linked to the prior completion fact through
   `supersedes_id` when one exists.
4. Flush so constraints and provenance writes are validated.
5. Retract all prior live rows captured in step 1.
6. Flush again and release the savepoint.

If any step fails, both the new writes and the old-row retractions roll back.
The prior generation is therefore still the only live generation. Batch stats
increment only after the per-document function returns successfully, so rolled
back work is not reported as processed.

Processing always writes all three managed facts: `country`, `escalation`, and
`topic`. `country` has `{"value": null}` when the current classifier detects
no country. This explicit leaf supersedes and retracts a prior country value.
It remains absent from country dashboards because those consumers already
accept only non-empty string values.

Processing replacement is restricted to the
`rule_based_document_classifier` extractor family. A fact of the same type
from another configured classifier or an analyst override stays live; one
pipeline must not retract another producer's evidence.

The rule-based document-classifier version moves from `2.0.0` to `2.0.1` even
though its classification rules are unchanged. Version `2.0.1` identifies the
corrected persistence behavior and makes every `2.0.0` document eligible once,
which is necessary to create missing null-country leaves and heal duplicate
live fact generations.

Mention replacement is scoped to one extractor **name** across its versions.
A successful `gazetteer_extractor` version bump retracts older live gazetteer
mentions and marker facts for that document. Rows from a differently named
extractor are left alone because separate extractor families may intentionally
coexist. Mention rows remain stored and retain their provenance; only their
current/live flag changes (P6).

## Consequences

- A forced mid-document failure leaves no partial derived rows.
- A failed replacement preserves the previous live generation without a
  repair job.
- A successful extractor-version bump leaves one live mention generation for
  that extractor name while retaining historical rows.
- A successful classifier replacement has one live fact per managed type,
  including an explicit null country result.
- The completion marker remains useful for selecting zero-mention documents,
  but atomicity no longer depends on marker ordering alone.
- Savepoints add two flush boundaries per document. That is a deliberate
  correctness cost; no new dependency or per-document network commit was
  added. If measurements show the extra flushes matter, they must be optimized
  without weakening the generation switch.
- This does not solve concurrent workers selecting the same document or give
  mentions a durable cross-version identity. The deployment currently
  serializes the pipeline; durable evidence identity belongs to M4.2.
- Rolling the code back is safe. Re-running the previous extractor version
  makes documents eligible again because its old marker was retracted, and it
  creates a new append-only generation rather than restoring rows in place.
