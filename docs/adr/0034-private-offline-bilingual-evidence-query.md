# ADR 0034: Private offline bilingual evidence queries

## Status

Accepted for local, offline use — 2026-10-12. Not a production integration or
authorization to publish source excerpts.

## Context

Main already has SQL-backed bidirectional entity search, Arabic romanization,
source-aligned language segmentation, and the domain `Document`, `Mention`,
`Entity`, and `ExtractorVersion` records. These pieces did not yet form a
bounded read interface that could answer an English/Arabic entity query and
show exactly where its Arabic evidence came from.

There is also an unshipped private bilingual persistence design at commit
`a3ab769` (`src/store/bilingual.py`, ADR 0031). It separates machine translation
artifacts/uses from romanization and verified aliases. That separation is
valuable, but adopting its schema or enabling stable entity generations is a
different task. The existing title cache is not an evidence-sentence cache:
a translated headline cannot truthfully be attached to an unrelated body span.

Public excerpt permissions, safe publication budgets, and a production
cache/export path are not settled by this local query feature.

## Decision

Add a standard-library-only read adapter in `src/search/evidence.py` and a
local JSON command in `src/search/evidence_cli.py`.

1. Reuse the existing core records, language registry, normalization,
   romanization, sentence segmentation, and exact/substring comparison rule.
   `name_matching.py` extracts that small comparison rule; the existing SQL
   search calls it too without changing its behavior.
2. Treat the input as a caller-supplied snapshot. The JSON format is a fixture
   and detached-read transport, not another database schema. `EntityMention`
   mirrors the existing link. IDs remain snapshot-local; there is no invented
   durable ID, identity adoption, generation pointer, or cross-generation merge.
3. Keep names explicitly typed as `original`, `romanization`, or
   `verified_alias`. An alias needs a verifier, attestation reference, and
   mention actually linked to that entity. This checks linkage, not the
   external truth of the attestation. Retracted witnesses remove that alias
   from the read view. No spelling resemblance creates a verified alias.
4. Validate the complete input before returning any result. Missing source or
   extractor references, duplicate IDs, inconsistent membership types,
   invalid offsets, stale hashes, ambiguous translations, and one live mention
   belonging to two live entities are errors. Historical retracted-entity
   memberships do not claim live ownership.
   `document.content_hash` is retained as ingest metadata, while a separate
   checked UTF-8 SHA-256 proves the exact loaded source text. We do not treat
   an ingest/dedup hash as a text hash.
5. Read sentence boundaries from each document's language adapter. Return a
   direct source slice with Unicode code-point offsets, source document ID,
   source label/URL, original text hash and length, span hash, mention and
   extractor identity, and segmenter name/version. Normalize for matching
   only. Source URLs reject unsafe schemes, credentials, malformed authority,
   control characters, and raw HTML attribute delimiters.
6. Bound results to at most 50 entities, five windows per entity, and 600
   source characters per window (defaults: 20, three, 280). A long sentence
   produces a `sentence_window` with separate full-sentence boundaries and
   `truncated=true`. A mention crossing sentence boundaries produces a
   `mention_span`, not a fabricated sentence. A mention larger than the cap
   is omitted and counted; it is never silently cut or allowed to bypass it.
7. Rank exact matches before substring matches, then original, verified alias,
   and romanization matches, then snapshot entity ID. Evidence is ordered by
   document ID and mention offsets/ID. Duplicate windows appear once. Caps
   report whether more matches/windows exist.
8. Accept a detached cached translation only when its artifact provenance,
   full document hash, exact span hash/offsets, and source language validate.
   The caller explicitly selects one artifact per span/target language.
   Content addresses follow ADR 0031: source hash, source/target language,
   provider/version, and settings hash. Conflicting outputs or artifact
   references fail validation. A whole-sentence translation is never applied
   to a cropped window. No provider is called and no cache miss is filled.
9. Preserve unavailable, cached empty, cached populated, oversized output, and
   synthetic fixture outcomes separately. The fixture-origin path is never
   labelled machine-generated. The shipped example contains **no translations**.
   Tests simulate detached cache rows, not real provider executions.

Input also has hard count and size limits: 500 documents, 1,000 entities,
10,000 mentions/memberships, 2,000,000 source characters in total, 20 aliases
per entity, 256 query characters, and an 8 MiB JSON file. Translation output
is limited to 1,200 characters per returned window; larger output is not
returned. These are local resource bounds, **not** approved public excerpt
or redistribution limits.

## Options considered

- **Extend the deployed dashboard/API now.** Rejected: publication policy,
  private-source access, real snapshot consistency, and production wiring are
  unresolved. A private reader is independently useful and testable.
- **Install ADR 0031's migration for this read feature.** Rejected: it would
  mix schema rollout and identity activation into a bounded query task.
  Instead preserve its artifact/variant distinction in detached read records.
- **Reuse any English title as evidence translation.** Rejected: language
  alone does not establish what exact source span was translated. The current
  title cache's narrow meaning must be preserved.
- **Translate during search.** Rejected: it introduces credentials, external
  data transmission, cost, nondeterminism, and unavailable-provider failure
  modes into an otherwise offline read.
- **Substring-search raw articles for the entity.** Rejected: existing
  entity-to-mention links are the available evidence. Re-extracting during a
  read could invent a membership or lose extractor provenance.
- **Treat romanization as verified English.** Rejected: a deterministic
  spelling candidate is a search aid, not a translation or human judgment.

## Consequences and limits

- No new dependency, ORM model, migration, database access, blob access,
  paid translation, public contract, UI, scheduler, or deploy is introduced.
- The reader returns an honest local checkpoint: bilingual lookup and cited
  Arabic evidence. It does not claim the complete M5 production checkpoint.
- The caller must load a coherent authorized snapshot and actually possess
  any claimed cache artifact/alias attestation. Hashes prove byte alignment,
  not who authored a record. This reader cannot prove that independently
  exported memberships were the live active generation at one instant.
- Real blob loading belongs in the existing document store. Missing/corrupt
  blobs must fail there, then this reader rechecks loaded text hash/length.
  This change does not fetch or test real R2/Neon data.
- ADR 0031's stored alias row alone does not supply a verifier and attestation
  reference. A future adapter must obtain that provenance rather than invent
  it or label every surface form a verified alias.
- Existing romanization has no independent semantic version. Results identify
  its implementation symbol and the versioned read adapter, and explicitly
  return `producer_version=null`. Romanizations here are ephemeral query hints,
  not newly persisted historical facts. Versioning it is a separate decision.
- Search retains the existing exact/substring baseline. It does not promise
  fuzzy recall, all Latin spelling variants, or translated entity names. The
  demo's `Mohammed al-Ahmad` is an explicitly fixture-attested alias; the
  current romanizer itself produces spellings such as `mohammed ahmad`.
- The output preserves external text verbatim. A future UI must render it as
  text and apply its own HTML escaping. A safe URL does not certify its source.

## Python notes for the checkpoint

- `@dataclass(frozen=True, slots=True)` gives the small read records generated
  constructors, makes their fields read-only, and prevents arbitrary new
  attributes. The reused core `Entity.properties` dictionary is not deeply
  immutable, but the reader neither reads it for alias authority nor mutates it.
- `tuple[...]` records a fixed collection of rows; a dictionary indexes those
  rows by ID so reference checks do not repeatedly search a list.
- `dataclasses.asdict` turns a result record into JSON-friendly dictionaries;
  it does not write to a database or file.
- Half-open offsets `[start:end]` include `start` and exclude `end`. Therefore
  `end - start == len(text)` in Python code points, even when UTF-8 uses more
  bytes for Arabic or emoji. A SHA-256 deliberately hashes UTF-8 bytes instead.
- `bisect_right(sentence_starts, mention.start) - 1` locates the last sentence
  whose beginning is no later than the mention. We still check that its end
  contains the complete mention before calling the window a sentence.
- Sorting with a tuple key compares fields from left to right. It makes
  pagination/caps reproducible when an export arrives in a different order.
- A `with path.open(...)` block closes the local file even if reading fails.
  JSON duplicate-key rejection prevents one ambiguous field from silently
  replacing another during decoding.
