# ADR 0033: An opt-in complete-current metadata archive beside dashboard v1

## Status

Implemented locally; production activation and consumer reconciliation deferred.
2026-10-12.

## Context

The current dashboard is intentionally a bounded view: recent articles and
country pages show samples, with country pages capped at 50 rows. Concatenating
those samples cannot produce a complete search corpus. It also drops documents
without a country. The unshipped public-atlas branch introduced a useful
separate archive producer, but its `complete: true` label did not define the
scope, count-only bundle checks could accept the wrong documents, and ordinary
READ COMMITTED queries could mix database generations.

Main now has current-generation safeguards, a versioned v1 producer/consumer
contract, and immutable-release promotion. The archive must preserve those
boundaries. Private evidence and bilingual source-span work remain separate.

## Options considered

1. **Join existing country samples in the browser.** Rejected: rows beyond
   country limits, unassigned countries, and undated records can disappear.
2. **Expand or replace dashboard v1 with a full new UI/data model.** Rejected:
   it would break independently maintained consumers and overlap unpublished
   desktop work.
3. **Publish full document/evidence text for rich search now.** Rejected:
   source bodies and exact evidence sentences require a separate reviewed
   privacy/provenance/consumer boundary.
4. **Add one explicitly scoped metadata artifact — chosen.** Keep v1 frozen,
   reuse main's current-document/current-mention selection, and export a
   separate file only when the caller explicitly opts in.

## Decision

`countries/_atlas.json` contains every current processed live document, with no
recent, country, date, or entity-membership limit. `complete: true` only has
meaning together with `scope: current_processed_live_documents`: latest topic
and escalation facts must both match the configured classifier version, and
the document must be non-retracted. Unassigned countries and absent publication
dates remain null. Unprocessed/retracted documents and superseded generations
are not historical archive entries.

The archive has a strict additive contract, its own schema and synthetic
fixture, a classifier name/version, and a content identity over canonical JSON.
That identity detects changed metadata even if a timestamp was reused. It is
neither a durable entity identity nor a stored historical snapshot. Existing
resolver row IDs are labeled `snapshot_local` explicitly. Only current live
mention memberships on included articles can supply an entity or its search
surface forms. Ambiguous live membership and mismatched object types fail the
bake rather than inventing a resolution. Historical entity properties are not
copied into public metadata.

Titles and names may include existing cached machine translations, explicitly
labeled as such. Cache misses stay null. Original Unicode is preserved; Arabic
normalization never rewrites original titles or names. Source bodies, exact
sentences, offsets, blob keys, and private evidence/translation artifacts are
not fields in the public contract. Unknown fields fail validation.

The producer checks exact selected document-ID membership. Bundle validation
also checks shared generation time, aggregates, country assignment, and the
actual v1 sample metadata with multiplicity. v1 does not expose IDs, so these
sample checks cannot establish more identity than v1 carries; the shared DB
snapshot and producer ID check supply the stronger boundary.

The explicit archive entry point uses a fresh PostgreSQL REPEATABLE READ,
read-only transaction for all v1 and archive reads. SQLite explicitly starts a
transaction before SELECT because its driver's legacy mode otherwise does not.
The connection is rolled back after reading. A low-level PostgreSQL archive
bake under READ COMMITTED is refused; a SQLite bake without an explicit
transaction is refused. Pending ORM writes are refused rather than flushed.

The archive is validated before any staging write and participates in the
existing rollback-safe country-directory swap, with `data.json` still the
last local commit marker. This prepares a local release candidate; it does not
replace immutable remote release promotion. Existing runtime collection of
`countries/*.json` includes the archive when present. Existing schedules,
`main.py`, default `run_bake`, and runtime stages do not opt in automatically.
The Worker only adds the exact new public archive URL to its existing CORS
allowlist; arbitrary JSON paths gain no new CORS behavior.

## Consequences

- A complete metadata search corpus can be built without changing dashboard v1
  or publishing source evidence.
- A caller must opt in with `include_atlas=True` or the dedicated local export
  module. Shipping/activation is a separate decision after consumer review.
- The full archive uses memory proportional to the selected metadata and
  memberships. There is no silent size truncation. No speedup or scaling claim
  is made, and no new library is added.
- Snapshot-local entity IDs are not stable user-facing bookmarks. Durable
  profile integration remains later work.
- Cached translation text alone does not expose full translation lineage.
  Private bilingual evidence must keep its existing stronger identity model.
- Offline contract, SQLite concurrent-read, malformed-membership, and rollback
  checks are executable without a service or API key. PostgreSQL isolation
  still needs verification in the project's dedicated PostgreSQL test target
  before production activation; no production database is used here.
