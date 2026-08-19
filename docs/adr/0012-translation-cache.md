# ADR 0012: Machine translation cached by source-text hash, not by document

## Context

M5 calls for Arabic → English translation so the platform is usable by
people who don't read Arabic — which is most of the people who will
evaluate it. Translation costs money per character (DeepL free tier: 500k
chars/month), so the same string must never be paid for twice.

## Options considered

1. **A `title_en` fact per document.** Fits the existing facts machinery
   with no new table. But it keys the translation to a *document*, so the
   same syndicated headline appearing on Al Jazeera, BBC, and CNN gets
   translated and stored three times — and a reprocess or a document
   retraction can orphan or duplicate it.
2. **A `translations` table keyed by `sha256(source_text)` — chosen.** The
   cache is content-addressed, exactly like blob storage (ADR 0006).

## Decision

Option 2. `TranslationORM` stores `(source_sha256, source_lang,
target_lang)` → `translated_text`, unique on `(source_sha256, target_lang)`.
`src/store/translations.py` checks the cache, sends only genuine misses to
the API, and writes results back under an `extractor_version_id`.

The brief's own words for why content-hash keying: it survives entity
merges. Nothing about a translation depends on which row happened to need
it, so nothing about it should break when that row changes.

## Consequences

- **Identical text is translated once, forever.** Syndicated wire copy is
  common in this corpus, so this is a real saving, not a theoretical one —
  verified by `test_identical_text_from_two_documents_translated_once`.
- **P4 holds** — every cached row records which translator version produced
  it, so a translator change is identifiable and re-runnable.
- **P1 is satisfied at one remove, and that's a deliberate call.** A
  translation has no provenance row of its own, because provenance chains
  terminate at a document *and character span*, and a content-hash-keyed
  translation is deliberately document-independent — that independence is
  the entire point. The chain is still walkable: document → title fact
  (which does carry provenance) → hash of its value → translation row
  (which carries an extractor version). Two hops instead of one, no broken
  links. If that indirection ever proves too clever in practice, the fix is
  to add a join table recording which documents a translation was fetched
  for — not to abandon content addressing.
- **Translations are never shown alone.** The dashboard renders the English
  beneath the Arabic, never replacing it, per M5's explicit requirement —
  a machine translation is a derived claim, and hiding the source would
  make it uncheckable.
- **Cost is capped per run** (`MAX_TRANSLATIONS_PER_RUN`, default 200).
  Overflow is left for the next run rather than failing; the UI degrades to
  Arabic-only, which is the pre-existing state, not a broken one.
- Only titles are translated. Bodies are ~50x the characters against a
  fixed monthly quota and nothing currently displays them. Evidence-sentence
  translation arrives with M5's entity work, when there's a reason to.
