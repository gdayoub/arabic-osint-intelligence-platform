# ADR 0018: Versioned public snapshot contract

## Status

Accepted — 2026-08-20

## Context

The scheduled pipeline publishes `data.json` and one JSON file per country.
The Cloudflare dashboard and the separate Vercel portfolio both consume that
public data, but the producer previously returned unvalidated dictionaries.
There was no machine-readable schema, canonical fixture, generated consumer
type, or deterministic bundle that could show whether a producer change was
compatible. The bake also wrote `data.json` before the country files, so a
late validation or filesystem failure could leave a mixed release.

External titles, names, sources, and URLs are untrusted input. Text must be
preserved exactly for evidence and translation provenance, while links must
be restricted to safe absolute web URLs. Escaping text in the producer would
destroy the original value and risks double escaping; HTML safety belongs at
the consumer's text-rendering boundary.

## Options considered

1. **Keep handwritten dictionaries and consumer-only tests.** Rejected
   because a producer can publish malformed data before either consumer sees
   it, and the same shape would remain duplicated in two repositories.
2. **Use JSON Schema alone.** Rejected because it is a good portable artifact
   but would require a second runtime validator or handwritten validation
   logic in Python. That would let the executable model and published schema
   drift apart.
3. **Use Pydantic's `HttpUrl` type for source links.** Rejected because it
   canonicalizes some valid inputs. Public evidence should validate a URL's
   scheme and authority without rewriting the stored source value.
4. **Escape HTML-sensitive characters while baking.** Rejected because the
   same JSON feeds non-HTML consumers, escaping changes evidence text, and a
   correct UI must use text nodes rather than trusting producer escaping.
5. **Strict Pydantic v2 models plus generated artifacts and staged writes —
   chosen.** One executable model validates production payloads and generates
   the portable contract bundle.

## Decision

`src/contracts/dashboard.py` is the executable producer contract. Every model
uses Pydantic v2 strict mode and rejects unknown fields. Counts, ranges,
cross-field totals, review distances, country indexes, timestamps, slugs, and
safe absolute `http`/`https` links are validated before any public file is
replaced. URL validation returns the original string unchanged. Other
external strings are also preserved unchanged and are never HTML escaped by
the producer.

The payload keeps `schema_version: 1` so both existing consumers remain
compatible. The independently versioned contract bundle starts at semantic
version `1.0.0` under `contracts/dashboard/1.0.0/`. A deterministic generator
emits dashboard and country JSON Schemas, canonical hostile-text fixtures,
generated TypeScript declarations, and a manifest containing each artifact's
size and SHA-256 plus a bundle hash. Check mode compares bytes and fails on
drift; it never silently repairs committed output. The root
`contracts/dashboard/contract.lock.json` is the deterministic current-version
pointer and pins the selected manifest and bundle hashes.

Pydantic is pinned to `pydantic==2.12.5`. The standard library can parse JSON
but cannot provide the nested strict validation and JSON Schema generation
needed here. Pydantic is already the project brief's required API boundary,
so this does not introduce a competing validation system.

The runtime bake validates the complete dashboard/country bundle, serializes
all files into a sibling staging directory, then promotes the country
directory and finally `data.json`. The main file is the commit marker: it is
never made visible before all country files exist. Existing generated targets
are backed up and restored if promotion raises, so a failed bake leaves the
previous snapshot intact.

## Consequences

- A malformed count, unsafe link, extra field, missing country file, or stale
  generated artifact fails before publication.
- The portfolio can vendor one immutable schema/fixture/type bundle and pin
  its semantic version and hash without changing its existing public route.
- Pydantic schema output can change between library releases, so reproducible
  generation depends on the exact pin and an intentional contract-version
  update when that dependency moves.
- Producer validation is not an HTML sanitizer. Both browser consumers still
  must render external strings as text and set links only after protocol
  validation; this contract makes unsafe URLs impossible but does not make
  `innerHTML` safe.
- Multiple filesystem paths cannot be swapped in one POSIX operation. Staging,
  rollback, and making `data.json` the last commit marker provide the practical
  atomic boundary for the existing URL layout; an immutable release manifest
  pointer remains the later F0 release-orchestration step.
