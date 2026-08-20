# ADR 0020: Publish immutable candidates through a monotonic promotion gate

## Status

Accepted — 2026-08-20

## Context

The dashboard build produces several files that together describe one view of
the database. Publishing those files one at a time under mutable public keys
can expose a mixture of two builds. A delayed GitHub job can also finish after
a newer job and silently replace new data with old data. A deployment process
can die after the hosting provider accepted a release but before the process
recorded success, so a simple exception handler cannot safely decide whether
the release is live.

Rollback has a different meaning from stale publication. An operator may need
to serve a known old payload again, but doing that must not reset the ordering
guard and make an already queued intermediate candidate look new.

## Options considered

1. **Overwrite mutable blob keys during every bake.** Rejected because a
   partial upload is externally visible and old jobs can overwrite new data.
2. **Order candidates by timestamps or GitHub job start time.** Rejected
   because clocks and queue order are not a serialization boundary.
3. **Wrap the database update and provider deployment in one transaction.**
   Rejected because the object store and hosting provider cannot participate
   in a PostgreSQL transaction. A connection failure after provider acceptance
   remains ambiguous.
4. **Immutable content-addressed candidates plus a database promotion gate —
   chosen.** Candidate bytes never change, the manifest is uploaded last, a
   row lock compares monotonic state before deployment, and an uncertain
   deployment remains pending for later observation.

## Decision

Every candidate consists of named `ReleaseArtifact` bytes with an explicit
HTTP content type. Artifact keys contain the byte SHA-256 and a short digest of
the content type, so identical bytes that must be served as different media
types cannot accidentally share incorrect object metadata. The manifest lists
each public path, content-addressed key, SHA-256, byte length, and content type.
It also records schema version, release ID, run ID, commit, creation time, and
data sequence. Canonical JSON makes its own SHA-256 reproducible, and its key
is derived from that hash.

Candidate construction appends `release_reserved` and uses that immutable
event's database ID as `data_sequence`. Gaps are valid; only strict monotonicity
matters. Artifact writes are read back and verified. The manifest is written
and verified only after every artifact succeeds, and
`release_candidate_created` is appended last. The database writes sit in a
savepoint, so an interrupted upload registers no candidate. Unreferenced
content-addressed blobs may remain and can be reused by a retry.

`publication_state` is one mutable, row-locked coordination record. It caches
the current release, current data sequence, the greatest successfully
published data sequence, the latest promotion sequence, and a complete pending
promotion plan. This row is reconstructable from immutable events and
manifests; it is not the audit record.

A normal promotion is rejected before provider access when its data sequence
is not greater than `max_data_sequence_seen`. `promotion_started` is committed
before calling the provider, and that event's ID becomes the monotonic
`promotion_sequence`. The adapter receives the complete verified artifact set.
Only an adapter observation that the exact release ID, manifest hash, and
promotion sequence are live permits `release_published` and advances current
state. A provable non-live result records `release_failed`. An exception or
unknown observation leaves the plan pending; a later reconciliation run asks
the provider what is actually live and completes or fails that same promotion.

Rollback is an explicit new promotion of a candidate that has a prior
`release_published` event. It records the promotion sequence being rolled back,
receives a new promotion sequence, and changes the current data sequence to
the selected old payload. It does not lower `max_data_sequence_seen`. Therefore
an intermediate queued candidate remains stale after rollback.

Revision `0003_publication_state` creates and seeds the singleton. It is
forward-only. The deployment adapter is a protocol; this checkpoint uses only
fake adapters and does not modify a workflow or contact a hosting provider.

## Consequences

- A manifest can never point at an artifact that candidate construction did
  not upload and verify first.
- A stale queued job is rejected by database state, independently of workflow
  concurrency settings.
- Provider ambiguity blocks another promotion until reconciliation instead of
  guessing and possibly overwriting a live release.
- Rollback is visible as a newer operational action while the data high-water
  remains truthful.
- Candidate blobs are append-only and can accumulate unreferenced uploads.
  Garbage collection is deferred until retention can be based on ledger
  reachability and measured storage cost.
- The mutable singleton adds coordination code and row locking, but it can be
  rebuilt from immutable evidence after a repair.
- No new Python dependency is required; hashing, canonical JSON, identifiers,
  and media-type handling use the standard library and the existing BlobStore.
