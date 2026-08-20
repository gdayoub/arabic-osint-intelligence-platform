# ADR 0016: Current public snapshot semantics

## Status

Accepted — 2026-08-20

## Context

The public bake counted one document as processed whenever any live `topic`
fact existed. Facts are append-only and the query did not join `documents`, so
facts belonging to a retracted document still contributed to processed, topic,
and escalation totals. That produced an impossible live snapshot in which
`total_processed` exceeded `total_raw`.

The same public file mixed current and historical derived data elsewhere.
Mention totals counted live mention rows even when their document was
retracted or a later extraction marker selected another extractor generation.
Entity totals counted rows whose only supporting mentions were no longer
current evidence.

The present schema identifies an extractor generation by
`extractor_version_id`, but it does not identify separate processing attempts
made with the same version. F0.1a needs an honest current view without taking
over F0.1b's transaction and attempt-atomicity work.

## Options considered

1. **Clamp `total_processed` to `total_raw`.** Rejected because it hides the
   contradiction while topic and escalation aggregates remain wrong.
2. **Count any non-retracted topic fact.** Rejected because it includes facts
   for retracted documents and stale extractor versions.
3. **Add a processing-run table now.** This is the eventual exact model, but
   rejected for F0.1a because it requires a schema migration and overlaps the
   separate transaction/version-switch checkpoint.
4. **Project current state from live documents, latest facts, completion
   markers, and extractor versions — chosen.** It uses information already in
   the schema and can be rolled back as one bake-query change.

## Decision

Every document fact exposed by the public bake first joins a non-retracted
document. Within that live set, the row with the latest `created_at` and `id`
is the current row for one document and fact type.

A document is currently processed only when its latest `topic` and
`escalation` facts both belong to the configured
`rule_based_document_classifier` version. `topic` remains the completion
marker because `process_one_document()` writes it last. `escalation` is also
required because every successful processing run produces it. `country` is
optional, but it is published only when its latest row belongs to that same
extractor version.

The processed document IDs are therefore a subset of live document IDs. The
bake checks `total_processed <= total_raw` and fails instead of publishing an
impossible count.

For extraction, the latest live `mentions_extracted` fact selects the current
extractor version independently for each live document and extractor name.
This preserves intentionally coexisting extractor families while excluding
older versions within each family. Mention counts, entity evidence counts, top
lists, and review evidence include only non-retracted mentions selected by
those markers. A live entity with no current live evidence is historical and
is not counted in the current public snapshot.

## Consequences

- Retracting a document immediately removes its facts, mentions, entities with
  no other current evidence, and review evidence from current public totals.
- Old extractor generations remain in Postgres for provenance but no longer
  inflate the current snapshot.
- `total_processed` counts distinct completed live documents rather than fact
  rows and cannot exceed `total_raw` under the selected predicate.
- The public view can distinguish extractor versions but not two attempts made
  with the same version. F0.1b must make each document attempt atomic and
  retire the previous live mention generation after a successful replacement.
- No dependency or schema change is introduced. The rollback boundary is the
  bake projection and this ADR; append-only stored history is untouched.
