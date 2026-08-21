# ADR 0030: Keep sentence and paragraph boundaries versioned and source-aligned

## Status

Accepted — 2026-08-21

## Context

M5 evidence artifacts need to show a bounded source sentence beside an
original mention.  M6 co-occurrence links will need to distinguish same
sentence from same paragraph.  Both features become wrong if a boundary is
calculated against normalized text, translated text, bytes, or a later
rewritten document body: the returned offsets must still obey P2 against the
original Unicode source text.

The current ingestion representation is also important.  Existing scrapers
usually flatten article HTML into one body string.  That representation no
longer proves where the publisher's original paragraphs were.  Splitting it
by length, sentences, or a guessed HTML layout would invent structural facts
that the source did not provide.

## Decision

Add an additive language-layer segmentation foundation only.

- `TextSpan` represents a half-open Python Unicode code-point range plus the
  exact substring sliced from the original text.  The normal construction
  path validates bounds with `TextSpan.from_source`; `assert_matches` makes
  the P2 proof explicit wherever the raw text is available.
- `LanguageAdapter` now publishes a `SegmentationSpec(name, version)` and
  implements `segment_sentences()` and `segment_paragraphs()`.  Any semantic
  change to a language's rules must bump its segmentation version before
  outputs are persisted or published.
- Arabic and English each own deterministic, dependency-free rule segmenters.
  They recognize the punctuation customary to their language, keep decimal
  points and common English titles/initialisms intact, include closing quotes
  with a terminal sentence, and retain an unfinished final sentence rather
  than discarding it.
- A paragraph exists only when the input contains an explicit blank-line
  separator (including CRLF forms).  A single newline remains part of its
  paragraph.  Flattened current scraper text therefore produces one
  paragraph, regardless of how many sentences it contains.
- The foundation is not wired into extraction, resolution, the scheduled
  pipeline, the database, or public snapshot files yet.  It provides the
  testable boundary contract that later M5/M6 work can consume.

## Alternatives considered

1. **Put Arabic punctuation checks in M5/M6 core code.** Rejected because it
   violates P3 and makes another language require graph or evidence changes.
2. **Normalize before segmenting.** Rejected because normalization can remove
   or fold characters and breaks P2 offsets.
3. **Use a third-party NLP sentence model now.** Rejected because a small,
   deterministic rule set is sufficient for this bounded foundation and adds
   no model/runtime dependency to the scheduled pipeline.  A measured future
   replacement is possible through a version bump.
4. **Infer paragraphs from sentences, character count, or current flattened
   HTML text.** Rejected because it fabricates evidence structure.  A future
   paragraph-preserving scraper representation must be a new document
   version/UID, not an in-place reinterpretation of existing source text.

## Consequences

- Future evidence snippets and graph scopes have a single language-owned
  interface and can record exactly which boundary behavior produced them.
- Golden tests cover Arabic and English punctuation, decimals, abbreviations,
  quotes, ellipses, Unicode offsets, CRLF blank lines, and the no-fabricated-
  paragraph rule.  Property tests prove every returned span is an exact slice
  of arbitrary original Unicode input.
- The v1 rules are intentionally conservative, not a full linguistic parser.
  Ambiguous abbreviations remain a known limitation and require a measured,
  versioned improvement rather than a silent behavior change.
- This is code-only and additive.  If it must be rolled back before another
  feature stores its outputs, revert this checkpoint; no migration, document
  rewrite, or public-contract rollback is needed.
