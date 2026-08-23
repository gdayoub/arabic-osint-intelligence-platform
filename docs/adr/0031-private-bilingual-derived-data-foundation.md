# ADR 0031: Private bilingual derived-data foundation

## Status

Accepted — 2026-08-23

## Context

The platform needs three genuinely different bilingual claims to live
side by side without collapsing into one ambiguous "alternate name" or
"English text" bucket:

1. **Machine translation** of a source span or surface into another
   language, via a paid external provider (ADR 0012's `TranslationORM` is
   the existing narrow case: Arabic → English titles only).
2. **Romanization** — a deterministic, local, zero-cost transliteration of
   Arabic script into Latin script (`ArabicAdapter.romanize()`, already
   used for M4 blocking-key generation). This is not a call to a provider
   and carries no translation cost or provider version.
3. **Verified alias provenance** — a human-confirmed alternate name for a
   stable entity, which is neither machine translation nor deterministic
   transliteration and must be attributable to whoever/whatever verified
   it.

If these three claims share one table or one "kind" enum with translation,
a reviewer (or a later migration) cannot tell, from the data alone, whether
an English string next to an Arabic name was paid-for machine output, a
free deterministic spelling, or a human judgment call. That ambiguity is
exactly what Global Constraint 7 (append-only history; repair by new
generation, not by mutation) and the existing evidence-identity layer
(ADR 0025) already refuse to allow for entity resolution — the same
standard needs to apply here.

Migration 0006 is scheduled inside Tranche A (Task 3) even though it is
conceptually the first Tranche B bilingual task, so that it becomes the
single Alembic head before Task 4 assigns 0007. Branching heads would make
every later migration ambiguous about which lineage it continues.

## Decision

Add three new, additive tables, all append-only (no `UPDATE`/`DELETE` at
the ORM level via `before_update`/`before_delete` listeners, and via a
PostgreSQL trigger reusing the same generic
`reject_stable_entity_history_mutation()` function migration 0005 already
created):

- **`TranslationArtifact`** — an immutable, content-addressed machine
  translation output. The content address is
  `(source_text_sha256, source_lang, target_lang, provider,
  provider_version, settings_sha256)`, unique-constrained. Two requests for
  the same address always resolve to the same row. `output_text` is a
  required `str`; an empty translation (`""`) is a normal, storable
  outcome distinct from "not yet attempted" (no row at all) — the
  repository layer rejects `None` outright to keep that distinction
  unambiguous.
- **`TranslationUse`** — one immutable pointer from a `TranslationArtifact`
  to the exact durable evidence span (`evidence_identity_id`, always
  required) it was applied to, and, when that surface came from a stable
  entity, the exact membership (`source_membership_id`) that produced it.
  A composite foreign key `(source_membership_id, evidence_identity_id) ->
  stable_entity_memberships(id, evidence_identity_id)` — the same pattern
  `StableEntityLineageEvidenceORM` already uses for lineage witnesses —
  makes it impossible to record a use whose membership and evidence
  identity belong to different generations or entities.
- **`StableEntityVariant`** — a durable alternate surface form attached to
  a stable entity. `variant_type` is `"romanization"` or
  `"verified_alias"` (a `CHECK` constraint enforces this closed set) —
  never `"translation"`. The same composite-FK pattern anchors every
  variant to the exact membership/evidence pair that produced it. A
  retracted-from-the-active-generation membership does not delete its
  variant rows (append-only history survives), but the repository's
  `active_variants()` reader filters strictly by
  `StableEntityResolutionStateORM.active_generation_id`, so a variant whose
  membership belongs to a superseded generation stops appearing as current
  without ever being deleted or rewritten.

  The composite foreign key only ties the membership to the evidence
  identity it witnesses — it cannot also express "and this membership
  belongs to *this* stable entity," because `stable_entity_id` isn't part
  of that key. `_add_variant()` therefore checks this explicitly in Python
  (`_membership_stable_entity_id()` looks up the membership's own snapshot
  and compares its `stable_entity_id` to the one the caller supplied)
  before writing, and raises `BilingualInvariantError` on a mismatch. This
  is a repository-layer invariant, not an additional database constraint —
  see `test_variant_rejects_a_membership_from_a_different_stable_entity` for
  the case it exists to catch.

`src/store/bilingual.py` is the only write/read surface: `get_or_create_*`
functions make content-addressed writes idempotent, `record_translation_use`
and `_add_variant` re-check the membership/evidence pairing in Python before
the DB constraint would reject it (a clear domain error instead of a raw
`IntegrityError`), and `romanize_stable_entity()` calls the existing
`ArabicAdapter.romanize()` directly — it never creates a
`TranslationArtifact`, keeping romanization and translation
distinguishable by construction, not just by convention.

### Retracted-membership exclusion applies asymmetrically, on purpose

`active_variants()` filters by `active_generation_id`, and
`active_translation_uses()` (scoped by `stable_entity_id`) mirrors it
exactly for membership-anchored uses. But a `TranslationUse` with no
membership at all — a use anchored only to `evidence_identity_id`, e.g. a
translated document title that was never claimed to come from any stable
entity — has no generation to retract from and is deliberately excluded
from that filtering entirely: it is a permanent fact about an evidence
span, not a claim about a stable entity's current state, so there is
nothing for a resolver generation advancing to invalidate. This is a
considered asymmetry, not an oversight: `StableEntityVariant` always names
a stable entity (so it always has a generation to go stale against);
`TranslationUse` sometimes doesn't.

## Alternatives considered

1. **One `alternate_names` table with a `source` string column.** Rejected:
   a free-text `source` column cannot be enforced by a `CHECK` constraint
   the way a closed `variant_type` enum can, and nothing would stop a
   caller from writing `source="translated"` for a value that was in fact a
   deterministic romanization.
2. **Extend `TranslationORM` (ADR 0012) with a `kind` column covering
   romanization too.** Rejected: that table's identity is title-only,
   Arabic→English, provider-backed content addressing. Overloading it to
   also mean "no provider, no cost, deterministic" would break its existing
   unique constraint's meaning and its cache-hit semantics for M5's actual
   translation pipeline.
3. **Let `StableEntityVariant` reference `evidence_identity_id` alone,
   without the membership composite FK.** Rejected: evidence identity is
   extractor-independent and generation-independent by design (ADR 0025),
   so an evidence-only reference cannot by itself prove which stable-entity
   generation produced the variant — exactly the gap the composite FK
   closes.
4. **Downgradable migration.** Rejected for the same reason 0005 refuses a
   downgrade: this is observed derived-data history, not disposable
   generation-local state. Recovery is a forward repair or a database
   restore, never an in-place delete.

## Consequences

- Migration 0006 is additive only — no existing table's columns change, and
  the public payload/contract is untouched. Old code that has never heard
  of `translation_artifacts`, `translation_uses`, or
  `stable_entity_variants` runs unmodified against the expanded schema.
- Translation, romanization, and verified-alias provenance are
  distinguishable directly in the schema (separate tables, and a `CHECK`-
  enforced `variant_type` split within `StableEntityVariant`), not only in
  code comments.
- A translation use or a stable-entity variant can never be attributed to
  the wrong evidence span, or (for a use) the wrong membership generation —
  enforced by the database's composite foreign key, not only by
  application code. A variant additionally can never be attributed to a
  stable entity its membership doesn't actually belong to — enforced by
  `_add_variant()`'s explicit repository-layer check, since the FK alone
  cannot express that constraint.
- 0006 remains the single Alembic head, so Task 4's first Tranche B runtime
  migration (0007) has exactly one place to attach to.
