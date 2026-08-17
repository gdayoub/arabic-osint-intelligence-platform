# ADR 0006: Content-addressed blob keys, and two separate hashes

## Context

`documents` now carries two different hash-shaped columns: `content_hash`
(from M1, computed as `sha256(source|title|body|url)` by the scrapers) and
`text_sha256` (new in M1.5, `sha256(text)`, used as the blob key). It would
be natural to ask why one hash isn't enough.

## Decision

Keep both, because they answer different questions:

- **`content_hash`** is a *dedup identity* — "have I already scraped this
  exact article from this exact URL." It includes the URL and source
  deliberately, so the same text served at two different URLs (a syndicated
  wire story on both Al Jazeera and BBC Arabic) produces two different
  `content_hash` values. That's correct: they're two separate pieces of
  evidence, and collapsing them would break M8's independent-source
  corroboration counting before M8 even exists.
- **`text_sha256`** is a *content address* — "what blob holds this exact
  text, and can I verify what I fetched matches what I wrote." It
  deliberately excludes source and URL, so the same text from two sources
  shares one blob. That's the point: it's about storage deduplication, not
  article identity.

Consequence made explicit here rather than left implicit: `documents` is
**non-unique** on `content_hash` (unlike legacy `raw_articles`, which is
unique on it) — see `src/store/orm.py`'s comment on `DocumentORM.content_hash`.

The blob key itself (`text_blob_key()` in `src/store/blob.py`) is built from
`text_sha256` with a `v1/` prefix and a 2-character fan-out directory
(`v1/documents/{hash[:2]}/{hash}.txt.gz`). The `v1/` means a future key
scheme change is a new prefix, not a migration of existing keys. The
fan-out exists only so `LocalDiskBlobStore` never dumps tens of thousands of
files into one directory — it is not for R2/S3 performance, which is an
outdated rule from early S3 that no longer applies.

## Consequences

- Two documents can point at the same blob (identical text, different
  source/URL). Retracting one of them (P6) must never delete or touch the
  blob — enforced by `retract_document()` only ever setting the `retracted`
  flag, never touching `text_blob_key`.
- No R2 lifecycle/expiration rule is configured, and none should be added
  without checking blob reference counts first — a naive TTL could delete a
  blob a still-active document still points to.
- Backfill and live ingest must compute `content_hash` and `text_sha256`
  identically, or the same historical article could produce two different
  documents depending on which path wrote it. `text_sha256` is deterministic
  by construction (pure function of `text`); `content_hash`'s correctness
  depends on both paths composing `source|title|body|url` the same way —
  the backfill script reuses `src.database.crud.compute_content_hash`
  rather than reimplementing it, for exactly this reason.
