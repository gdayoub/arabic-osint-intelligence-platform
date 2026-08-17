# ADR 0004: Document text moves to object storage behind a `BlobStore` interface

## Context

`documents.text` (`src/store/orm.py`, from M1) stores article text inline in
Postgres. That's simple, but it's also the single largest source of storage
growth once the pipeline is hosted for real (M1.5): Postgres free tiers
(Neon: 0.5 GB) are priced for indexed, queryable metadata, not for bulk text
that's only ever read whole, never filtered or joined on.

## Options considered

1. **Leave text inline in Postgres.** Simplest, no new moving parts. Doesn't
   solve the actual problem — this is the status quo being replaced.
2. **Postgres `bytea` with compression.** Keeps everything in one database,
   but still counts against the same storage tier the metadata needs to fit
   in, and Postgres isn't priced or optimized for bulk blob storage the way
   R2/S3 are (R2's free tier alone is 20x Neon's free Postgres tier).
3. **R2 (or S3) called directly wherever text is read or written.** Solves
   the storage problem, but every caller (`create_document`, ingest,
   backfill, `get_provenance_chain`) ends up importing boto3 and knowing R2
   exists. Swapping providers later means hunting down every call site.
4. **R2 behind a narrow `BlobStore` interface — chosen.** One module
   (`src/store/blob.py`) knows boto3 exists. Everything else calls
   `put`/`get`/`exists` on whatever `get_blob_store()` returns.

## Decision

Option 4. `src/store/blob.py` defines `BlobStore` as a `typing.Protocol`
(structural typing — matches the vocabulary the brief already uses for
`LanguageAdapter` in M2 and `MentionExtractor` in M3, rather than
introducing ABC as a second convention). Two implementations exist from the
start: `LocalDiskBlobStore` (default, no credentials or network — a fresh
clone and the whole test suite work with zero setup) and `R2BlobStore`
(boto3, selected via `BLOB_BACKEND=r2` plus four env vars).

Keys are **content-addressed**: `text_blob_key()` derives the key from
`sha256(text)`, not from a document id. This means a write is naturally
idempotent (`exists()` before `put()` skips redundant uploads), two
documents with identical text share one blob for free, and the key is known
before the database row exists — so there's no ordering dependency between
the blob write and the Postgres insert. (The next stage, wiring this into
`create_document()`, will make blob-first-then-row the write order
precisely because of that: a stray orphan blob is harmless, but a Postgres
row pointing at a blob that was never written is a P1 violation — the
provenance chain couldn't terminate at real source text.)

`get()` on a missing key always raises `KeyError`, regardless of backend —
`R2BlobStore` catches boto3's `ClientError` and translates it. If backend
exceptions leaked through, every caller would need to know which backend it
was talking to just to handle "not found," and the abstraction would be
pointless.

## Consequences

- `Document.text` in `src/core/models.py` stays a plain `str` and is
  unchanged by this ADR — text is always fully loaded before a `Document` is
  constructed, never lazily fetched from inside the dataclass. A lazy
  accessor was considered and rejected: `src/core/models.py` is documented
  as importable with nothing but the standard library, and a lazy property
  would need a store handle inside a frozen core dataclass, breaking that
  separation (ADR 0001) and letting a plain attribute access fail with a
  network error.
- Extra indirection: every blob read/write goes through one more function
  call than talking to boto3 directly would. In exchange, `LocalDiskBlobStore`
  makes every test in this repo runnable offline, and a future S3/GCS
  backend is one more class plus one more branch in `get_blob_store()`,
  touching nothing else.
- boto3 is a real, justified new dependency (~50 MB installed): the
  alternative is hand-rolling AWS SigV4 request signing against R2's S3-
  compatible API, which is fiddly and produces opaque 403s when subtly
  wrong. Mitigated by keeping boto3 out of the dashboard-only requirements
  split (M1.5 Stage 6) so it's only installed where it's actually used.
