# Software Engineering Feature Roadmap and UI Handoff

**Status:** Proposed amendment; not yet the implementation spec

**Audience:** George, the software-engineering agent, and the UI agent

**Scope of this document:** Product direction, engineering features, public
data contracts, and acceptance criteria. It does not authorize a visual
redesign or change any UI code.

### Governing rule

`docs/AGENT_BRIEF.md` remains the specification of record. Its milestones must
still be completed in order. This roadmap proposes corrective gates before M5
and product-oriented additions inside M5–M10; it does not silently replace,
skip, or reorder any milestone. George must approve the mapping in section 5
before an agent implements it. Every approved slice still follows the brief:
explain first, write the ADR, stop at the checkpoint, and wait for George.

## 1. The product we should build next

The next version should not feel like a long page of Arabic news and charts. It
should feel like an investigation tool that happens to use Arabic evidence.

The user should be able to:

1. understand what changed in English within 30 seconds;
2. search for a person, organization, location, or story in English or Arabic;
3. inspect the original Arabic beside a clearly marked machine translation;
4. see why the system believes an entity, relationship, or story exists;
5. correct the model and see that correction survive later pipeline runs; and
6. share or export an evidence-backed result.

The strongest interview story is not “I made another dashboard.” It is:

> I built a cost-aware bilingual intelligence system that turns Arabic source
> documents into versioned entities, evidence-backed stories, and a queryable
> graph. Every result is traceable to an exact source span, and human feedback
> measurably improves the resolver.

## 2. Honest current baseline

What is already real and worth leading with:

- scheduled multi-source ingestion through GitHub Actions;
- content-addressed, compressed source text in Cloudflare R2;
- metadata, facts, entities, decisions, and provenance in Neon/Postgres;
- offset-safe Arabic normalization behind a language protocol;
- measured gazetteer and CAMeLBERT extraction baselines;
- blocking, learned pair scoring, guarded Union-Find clustering;
- a visible review queue with append-only must-link/cannot-link decisions;
- cached Arabic-to-English title translation; and
- one static data product served by Cloudflare and consumed by both the
  Cloudflare dashboard and the Vercel portfolio.

What is not yet strong enough to claim:

- The learned scorer currently produces zero production merges. The training
  set is small and many negative examples never survive inference blocking.
- Entity IDs are generation IDs, not stable real-world identifiers. Resolver
  recomputes retract and recreate entities.
- Only recent titles are translated. Entity names, evidence sentences, and
  review context are still Arabic-only.
- There is no real bilingual retrieval, entity dossier, story clustering,
  independent corroboration, relationship graph, or investigation export.
- The public UI says “real-time,” but the product is a scheduled snapshot.
- The live snapshot currently reports more processed documents than raw
  documents. A presentation layer must not hide that data-quality defect.
- Per-document processing/extraction catches exceptions without a savepoint.
  A failure can leave partial writes in the outer transaction, and an
  extractor-version bump can add new live mentions without retiring the old
  generation.
- The snapshot has `schema_version: 1`, but there is no enforced schema or
  shared compatibility test across the Cloudflare and Vercel consumers.
- `create_all()` can create a missing table but cannot migrate an existing
  Neon table. M5/M6 schema work needs a real, repeatable migration path.
- Pipeline and review deployments use different concurrency groups. They can
  publish snapshots out of order and restore an older review queue.
- The two repositories carry separate dashboard copies and can drift.

## 3. Product principles

### 3.1 English explains; Arabic proves

English should be the default reading layer for an English-speaking evaluator.
The exact Arabic must remain visible beside it as the original evidence. Never
replace, rewrite, or normalize stored Arabic for display.

### 3.2 Progressive disclosure instead of one infinite wall

The first screen should answer “what changed and why should I care?” Search,
entities, stories, graph exploration, review, and system health are separate
workflows. Technical model details should be available without competing with
the primary reading path.

### 3.3 Every conclusion has evidence

An entity, relationship, alert, credibility measure, or summary is unfinished
until the user can reach the source article, exact mention, extractor version,
and relevant source excerpt.

### 3.4 Quality is a feature

Freshness, translation coverage, source failures, model precision, review
volume, and unresolved ambiguity should be visible. Honest limitations are a
stronger engineering signal than an unexplained confidence score.

### 3.5 Static first, split payloads when needed

Keep the free scale-to-zero architecture. Bake public artifacts in the
pipeline. Do not expose Neon or R2 credentials to browser code. Keep
`data.json` small and move detailed entities, stories, search, and graph data
into separate static JSON files.

## 4. Target information architecture for the UI agent

This is an information hierarchy, not a visual design prescription.

1. **Overview**
   - snapshot freshness and source coverage;
   - “what changed” over 24 hours and 7 days;
   - high-priority stories and entity spikes;
   - English-first intelligence feed with Arabic originals.
2. **Search / Explore**
   - one search box for Arabic, English, aliases, and romanizations;
   - filters from configured object types/source facets, currently including
     source, country, topic, escalation, and date;
   - result count, sorting, reset, and shareable URL state.
3. **Entity dossier**
   - English/romanized name, Arabic canonical name, aliases;
   - first/last seen, source diversity, trend, timeline;
   - evidence excerpts, story membership, relationships, provenance.
4. **Story dossier**
   - bilingual representative title;
   - near-duplicate articles grouped together;
   - distinct-publisher coverage, timeline, entities, and evidence;
   - independent-source count only after source dependencies are modeled.
5. **Graph**
   - evidence-backed neighbors and paths;
   - filters by edge type, weight, time, and source;
   - every edge opens its supporting evidence.
6. **Model QA**
   - review queue, resolution explanation, label progress, model metrics;
   - this is a technical workflow, not the product’s opening section.
7. **System health**
   - latest pipeline run and stage durations;
   - per-source success, article yield, failures, data freshness;
   - current model/extractor versions and translation coverage.

## 5. Proposed mapping to the controlling milestones

The order below is the only proposed execution order. The `F` items are
corrective gates discovered while auditing the already-built M1–M4 code. They
are proposed amendments that must be approved before M5 starts. The `M` items
preserve every requirement and the exact order in `AGENT_BRIEF.md`.

| Order | Proposed slice | Relationship to the brief | Approval status |
|---|---|---|---|
| F0.1 | Derived-data correctness and retraction safety | Corrective gate for M1–M4 invariants | Proposed |
| F0.2 | Expand/migrate/contract schema path | Corrective gate before M5/M6 schema work | Proposed |
| F0.3 | Versioned public contract and render safety | Corrective gate for the two current consumers | Proposed |
| F0.4 | Immutable publication and run ledger | Corrective operations gate | Proposed |
| M4.1 | Production-shaped resolver learning | Completes M4's learned-scoring claim honestly | Proposed amendment to M4 checkpoint |
| M4.2 | Stable entity/evidence identity and constraint remapping | Makes M4 decisions durable enough for M5/M6 | Proposed amendment to M4 checkpoint |
| M5.1 | Bilingual derived-data model | Required M5 work | Required, detail proposed |
| M5.2 | Bidirectional search and entity evidence artifacts | Required M5 work plus an additive dossier contract | Required, detail proposed |
| M5.3 | Bilingual review data | Product addition using M4/M5 outputs | Proposed addition inside M5 |
| M6.1 | Evidenced co-occurrence graph and traversal | Required M6 work | Required, detail proposed |
| M6.2 | Typed relations and required graph metrics | Preserves the rest of M6 | Required |
| M7 | Events and geography | Preserved exactly; not replaced by stories | Required |
| M8 | Story deduplication, source coverage, and reliability | Preserves M8 and strengthens identity semantics | Required, detail proposed |
| M9 | Temporal analysis and backend alerting | Preserves both burst methods and real watchlist evaluation | Required, detail proposed |
| M10 | API/frontend contract plus investigation export | Preserves M10; export is additive | Required plus proposed addition |
| M11 | Access control | Preserved and still last | Required |

This mapping intentionally does not move story clustering ahead of the graph,
does not omit events/geography, and does not replace backend alerting with
browser storage. If George rejects an `F` or additive item, the original brief
still controls without ambiguity.

## 6. Engineering feature specifications

### F0.1–F0.3 — Data truth, migrations, security, and public contract

#### User outcome

Counts agree, public data is safe to render, and either public consumer fails
in CI before an incompatible snapshot ships.

#### Software-engineering work

- Define `total_processed` as the count of distinct live document IDs whose
  latest required processing generation completed successfully and whose
  required current facts all belong to that generation. It is not a fact-row
  count. Test that exact predicate and also assert
  `total_processed <= total_raw`.
- Make current-generation and retraction filtering consistent for facts,
  mentions, entities, evidence, and every aggregate.
- Wrap each document’s process/extract writes in a nested transaction or
  savepoint. A failed document must leave no partial facts or mentions, and a
  failed replacement must leave the previously live generation current.
- On extractor-version changes, explicitly retract/supersede the prior live
  mention generation before the new generation becomes current.
- Add a versioned migration mechanism before changing the production schema.
  Alembic is the justified default: it is the SQLAlchemy-standard migration
  tool, stays out of the pipeline hot path, and solves a problem `create_all()`
  cannot solve. Record the choice and simpler alternatives in an ADR first.
- Use an explicit **expand → migrate/backfill → contract** release sequence.
  CI validates empty-to-head and prior-to-head upgrades, but does not silently
  apply production migrations. The ordered release workflow owns the Neon
  apply step, records the revision, verifies the new application can run while
  old fields remain, and only contracts after both consumers have moved.
  Failed expands stop deployment; failed backfills resume idempotently;
  destructive changes use a forward repair or database restore, never an
  assumed Alembic downgrade. Record the recovery and rollback plan per change.
- Define the baked snapshot with Pydantic and emit JSON Schema.
- State additive compatibility rules and bump `schema_version` only with a
  documented migration.
- Make this repository's `contracts/dashboard/` directory authoritative for
  the JSON Schema, canonical fixture, generated TypeScript types, and manifest.
  Publish each contract bundle under an immutable semantic version and SHA-256.
  The portfolio vendors that exact bundle and records its version/hash in a
  lock file. Before promotion, this repository's compatibility workflow checks
  out the portfolio's pinned main revision and runs its consumer test against
  the candidate; the portfolio also tests its vendored fixture in its own CI.
- Escape every external string before HTML insertion, not only review data.
- Validate every external URL protocol (`http:` or `https:` only).
- Add production smoke tests for both public URLs.
  The portfolio acceptance URL remains
  `https://george-dayoub-portfolio.vercel.app/osint-dashboard.html`; additive
  contract work must not redirect or break that route.
- Assign every candidate an immutable `release_id` and monotonic
  `data_sequence`. Bake every split file and its SHA-256 into one manifest and
  upload the candidate as one immutable artifact. A single shared deployment
  queue reads the current manifest and rejects a normal candidate whose data
  sequence is not above `max_data_sequence_seen` *before* promotion. Every
  successful promotion receives a new monotonic `promotion_sequence` and
  publishes the complete Worker/assets version atomically. Shared concurrency
  prevents simultaneous promotion; the compare-and-publish guard, not
  concurrency alone, enforces ordering. An intentional rollback creates a new
  promotion pointing to an identified old payload, records `rollback_of`, and
  preserves the data high-water mark. It never republishes the old sequence as
  though time moved backward.
- Extend the Worker’s CORS policy deliberately to future public split JSON
  assets rather than assuming `/data.json` is the only cross-origin file.
- Choose one source of truth for dashboard markup. Until that decision lands,
  CI must detect drift between the Cloudflare and portfolio copies.

#### Acceptance criteria

- A test proves `total_processed` equals the distinct-document completion
  predicate; a bake also fails when processed count exceeds raw count.
- A forced mid-document failure leaves zero partial derived rows.
- If replacement generation N+1 fails, generation N remains the only current
  generation. A matrix test covers live/retracted facts, mentions, entities,
  evidence, and their public aggregates.
- A migration test passes from an empty database and from the previous schema
  revision to head.
- A release rehearsal proves the old application can run after expand, the
  backfill can resume, and contract waits for both consumer pins.
- A removed or renamed required field fails both consumer test suites.
- `data.json` contains no document bodies, credentials, or private metadata.
- A fixture containing HTML/script characters renders as text, never markup.
- Cloudflare and Vercel pass the same snapshot contract and browser smoke test.
- A publication-order test presents a newer release first and proves an older
  queued candidate is rejected; an interrupted upload never exposes a partial
  manifest.
- A rollback test promotes an old payload with a new promotion sequence, then
  proves a previously queued stale data candidate is still rejected.

### F0.4 — Pipeline run ledger and source health

#### User outcome

The user can tell whether the data is fresh and whether one source silently
stopped producing articles.

#### Software-engineering work

- Use append-only run events rather than pretending one immutable row can know
  both its start and terminal state. Event types include `run_started`,
  `stage_started`, `stage_succeeded`, `stage_failed`, `run_succeeded`,
  `run_failed`, `release_published`, and `release_failed`.
- Every event records run/release ID, commit SHA, event time, stage, relevant
  input/output/error counts, and extractor versions. Durations and current
  status are projections over those immutable events.
- Give a run a lease/heartbeat. If no terminal event arrives before its
  configured deadline, the next monitor emits `run_abandoned`; a killed runner
  is never misreported as successful merely because it could not write a final
  event.
- Record per-source attempts, inserted rows, selector/parsing failures, and
  latest successful article time.
- Bake a small `system_health` object describing the data stages of that
  release candidate. The separately promoted current-release manifest reports
  `published_at` and proves which release is actually live. A release cannot
  claim its own deployment succeeded before deployment happens.
- Treat stale data, zero-yield sources, count invariant failures, and failed
  stages as explicit states rather than a generic green dot.

#### Acceptance criteria

- A deliberately broken source fixture produces a visible source failure.
- A partial run cannot appear as a fully healthy snapshot.
- A killed-run fixture becomes abandoned after the lease deadline.
- Public `system_health.release_id` matches the manifest-selected release, and
  its stage statuses refer to that last successfully published release rather
  than a currently executing run.
- Health state explains the failing source/stage without leaking stack traces
  or secrets.
- Stage durations and counts are queryable for regression comparisons.

### M4.1 — Production-label learning loop

#### User outcome

Human review changes a measured production model instead of only filling an
audit table.

#### Software-engineering work

- Sample labels from pairs that survive real blocking. Include uncertain,
  high-score, low-score, and diverse-name buckets rather than only a fixed
  near-threshold band.
- Create a frozen audit set from an independently sampled, stratified slice of
  the blocking-surviving candidate universe before training. Never train on it
  while it is the active holdout. Record bucket populations, sampling
  probabilities, and label instructions so reported metrics are not only about
  pairs the current model chose to show a reviewer.
- Maintain a second pre-blocking gold set made from labeled entity clusters or
  sampled mention pairs from the full comparison universe. Use this set only
  to estimate blocking recall; a blocking-surviving audit set cannot reveal the
  true pairs that blocking discarded.
- Prevent one frequent name or source from consuming the queue.
- Export reviewed features with scorer version, candidate-universe version,
  sampling reason, and sampling probability.
- Split evaluation by time/source or entity group so near-duplicate mentions
  cannot leak between train and test.
- Report precision, recall, PR-AUC, calibration, and model-originated merges.
- Compare against exact-match and gazetteer baselines.
- Gate a weight deployment on a fixed precision floor chosen in an ADR.
- Pre-register minimum positive/negative holdout support and the uncertainty
  method from a prospective power calculation. Compare candidate and baseline
  on the same audit pairs with paired confidence intervals. Do not claim lift
  unless the lift interval excludes zero and the one-sided precision bound
  clears the deployment floor; insufficient support means “not proven,” not a
  win.
- Store each scorer as an immutable artifact containing weights, feature-schema
  hash, blocking version, threshold, training-label snapshot hash, code commit,
  random seed, evaluation-set ID, and metrics. Keep prior artifacts addressable
  for immediate rollback.
- Start with 75–150 production-shaped decisions, then decide from the learning
  curve whether more labeling is justified.

#### Acceptance criteria

- Every training pair could have occurred at inference.
- Evaluation can be reproduced from one command and a fixed seed.
- The frozen audit set remains disjoint from training data and its sampling
  design is included in the report.
- Train/test groups are split by normalized name/entity group, not random rows
  that can leak near-duplicates across the boundary.
- Blocking recall is reported on the separate pre-blocking gold set, with a
  target of at least 0.95 before scorer quality is treated as meaningful.
- The model's paired lift interval over the deterministic baseline excludes
  zero on the frozen, production-shaped audit set, and its precision lower
  bound clears the floor.
- A report separates merges produced by the model from exact/gazetteer merges.
- If no lift exists, the old weights remain deployed and the report says so.

### M4.2 — Stable entity identity, evidence identity, and versioned lineage

#### User outcome

An entity URL, watchlist entry, or graph node survives scheduled resolver
recomputes, including merges and splits.

#### Software-engineering work

- Separate durable real-world identity from resolver-generation clusters.
- Record append-only `continued`, `merged_into`, and `split_into` lineage.
- Assign each ingested document a persistent, never-reused `document_uid`.
  Give equivalent extracted evidence a durable fingerprint based on that UID,
  source-text SHA-256, original start/end offsets, configured object type, and
  language. Extractor-version IDs remain provenance but are not part of this
  continuity key, so the same exact span can be remapped after re-extraction.
  Including the document UID prevents identical syndicated bodies from sharing
  one constraint endpoint.
- Store must-link/cannot-link endpoints against durable evidence fingerprints,
  with the original mention IDs retained as provenance. On a version bump,
  exact fingerprint matches remap automatically. Missing or shifted spans are
  marked unresolved for review; the resolver must never guess a remap.
- A cannot-link blocks an automatic merge. Conflicting active must-link and
  cannot-link constraints halt that component and create a visible conflict
  item; recency does not silently overwrite an append-only decision.
- Reconcile each new generation against the prior generation using stable
  evidence mentions and explicit human constraints.
- A sensible starting rule is mention-overlap continuity: unchanged clusters
  keep their UID; a merge keeps a deterministic predecessor UID; the largest
  child of a split keeps the old UID and other children receive new UIDs. The
  exact semantics require an ADR because they become public identity behavior.
- Connect the currently unused entity `supersedes_id` concept or replace it
  with a clearer lineage table through an ADR.
- Support an “as of” query for identity membership and canonical name.

#### Acceptance criteria

- One stable entity identifier survives three full recomputes with unchanged
  evidence.
- A merge and a split have deterministic, explainable lineage.
- An old entity URL resolves to the current identity and can show its history.
- A version-bump test creates replacement mention rows for the same source
  spans and proves old must-link/cannot-link decisions still apply.
- A shifted-span fixture becomes an explicit unresolved constraint rather than
  applying to the wrong evidence.
- Two documents from different sources with byte-for-byte identical text and
  offsets receive different evidence fingerprints and constraints.

### M5.1 — English-first bilingual contract and bidirectional search

#### User outcome

An English-speaking user can understand the product and search “Mohammed,”
while an Arabic search reaches the same underlying entity.

#### Software-engineering work

- Extend translation beyond titles to canonical entity names, aliases, and
  bounded evidence sentences using the existing cache. Key a cached result by
  source-text hash, source language, target language, provider/model version,
  and settings hash; a content hash alone cannot distinguish different
  translation outputs.
- Budget translation by characters and user priority, not only string count,
  so one run cannot unexpectedly consume the free monthly quota.
- Record where each cached translation is used so the translated display value
  still has direct document/mention provenance; cache reuse alone is not a
  provenance edge.
- Generate multiple full-name romanization candidates through the language
  adapter. Store adapter name/version/settings with each candidate. Never use
  romanized text as stored source evidence.
- Keep verified English aliases, machine translations, and transliterations as
  different value kinds. A transliteration of a proper name is not presented
  as though a translator or a person verified it.
- Add versioned `segment_sentences()` and `segment_paragraphs()` behavior to the
  `LanguageAdapter`; evidence boundaries and later graph scopes must not embed
  Arabic-specific punctuation rules in core code.
- Bake `search-index.json` separately from `data.json`.
- Index Arabic original, normalized comparison terms, English translation,
  romanizations, aliases, object type, source, date, and searchable facets
  declared by ontology/source configuration. `topic`, `country`, and
  `escalation` are current configured facets, not hardcoded core fields.
- Evaluate retrieval with a hand-labeled bilingual query set.
- Preserve existing snapshot fields while consumers migrate to explicit
  `*_ar` and `*_en` fields.

#### Additive view model

```json
{
  "id": "stable-entity-id",
  "object_type": "person",
  "name_ar": "محمد أحمد",
  "display_name_en": {
    "value": "Mohammed Ahmed",
    "kind": "machine_transliteration",
    "generator": "arabic_adapter",
    "version": "1.1.0"
  },
  "romanizations": [
    {"value": "mohammed ahmed", "generator": "arabic_adapter", "version": "1.1.0"},
    {"value": "muhammad ahmad", "generator": "arabic_adapter", "version": "1.1.0"}
  ],
  "aliases_ar": [],
  "aliases_en": [],
  "translation": {
    "status": "machine_translated",
    "extractor": "deepl_translation",
    "version": "1.0.0",
    "source_language": "ar",
    "target_language": "en",
    "settings_hash": "sha256:..."
  }
}
```

#### Acceptance criteria

- At least 50 hand-labeled Arabic/English queries report Recall@1 and Recall@5.
- English and Arabic variants of a test name return the same stable entity.
- Search remains useful when translation is unavailable.
- No normalized string replaces source text or changes mention offsets.
- Translation coverage and failure count are visible in system health.

### M5.2 — Evidence-first entity artifacts

#### User outcome

The user can answer “who is this, why is this entity trending, and what source
evidence supports it?” without scanning the full feed.

#### Software-engineering work

- Bake one small file per stable entity: `entities/{id}.json`.
- At M5, include bilingual name data, aliases, configured object type,
  first/last seen, mention count, and distinct-publisher coverage. Add links,
  event geography, stories, and 24-hour/7-day trend only when M6, M7, M8, and
  M9 respectively complete; missing future capabilities are explicit, not
  invented as empty intelligence.
- Extract a bounded sentence around selected evidence mentions. Store exact
  full-document offsets and highlight the original mention. Use the versioned
  language adapter for sentence boundaries.
- Translate the bounded sentence through the content-hash cache.
- Rank evidence for source diversity, recency, and relevance; do not publish
  complete document bodies.
- Include a compact resolution explanation and provenance references.
- Treat the public evidence-snippet boundary as an ADR: sentence-only,
  allowlisted fields, explicit maximum length, and regression tests against
  accidental full-body disclosure.

#### Evidence item contract

```json
{
  "evidence_fingerprint": "sha256:...",
  "mention_id": 104,
  "document_id": 22,
  "text_ar": "محمد أحمد",
  "start_offset": 120,
  "end_offset": 129,
  "sentence_start_offset": 96,
  "sentence_end_offset": 168,
  "sentence_ar": "...",
  "sentence_en": "...",
  "segmenter": {"name": "arabic_adapter", "version": "1.1.0"},
  "sentence_translation": {
    "translation_id": 81,
    "translation_use_id": 204,
    "provider": "deepl",
    "version": "1.0.0",
    "settings_hash": "sha256:..."
  },
  "title_ar": "...",
  "title_en": "...",
  "source": "BBCArabic",
  "url": "https://...",
  "published_at": "2026-08-20T12:00:00Z",
  "extractor": {"name": "gazetteer_extractor", "version": "1.0.0"}
}
```

#### Acceptance criteria

- Every dossier fact links to at least one public evidence item.
- `document_text[start_offset:end_offset] == text_ar` remains true.
- `document_text[sentence_start_offset:sentence_end_offset] == sentence_ar`,
  and the mention bounds fall inside those sentence bounds.
- `translation_use_id` resolves through the cached derived value and extractor
  version to that exact Arabic sentence; cache reuse cannot break the chain.
- Evidence selection includes more than one source when available.
- No dossier file contains a complete article body.
- A retracted document is removed from current evidence without erasing its
  historical provenance.

### M5.3 — Better review workbench data

#### User outcome

A reviewer can make a fast, informed decision without reading Arabic fluently
or interpreting six unexplained floating-point values.

#### Software-engineering work

- Add bilingual article titles, romanizations, and bounded bilingual context
  to each side of a review pair.
- Include model/scorer version, queue sampling reason, and prior decisions.
- Bake a deterministic plain-English explanation from feature values, such as
  “names are moderately similar, but context does not overlap.”
- Keep raw features available as technical details.
- Track queue age, decision throughput, accept/reject rate, and label coverage.
- Preserve the strict GitHub issue title contract and owner-only write bridge.

#### Acceptance criteria

- A reviewer can identify both sources and contexts without opening an article.
- Explanation text is deterministic and covered by threshold-boundary tests.
- Retrying one GitHub issue cannot append a duplicate decision.
- A decided pair disappears from pending data after deployment.
- The next resolver generation reports whether and how the constraint applied.

### M6.1–M6.2 — Provenanced links and the graph

#### User outcome

The user can ask “what connects these entities?” and inspect the exact evidence
behind each edge.

#### Software-engineering work

- Start with weighted `mentioned_with` links: same sentence > same paragraph >
  same document.
- Add explicit link evidence containing both mention IDs, the document ID,
  scope, weight, extractor version, and retraction state. The current `links`
  row alone cannot explain the two spans that created a co-occurrence.
- Obtain sentence/paragraph scope from the document's versioned
  `LanguageAdapter`, not Arabic punctuation embedded in graph code.
- Record extractor version and source mention provenance for every link.
- Add configured typed relation extraction for at least `member_of` and
  `located_in`, with confidence and exact evidence spans. Relation types remain
  in `ontology.yaml`, not SQL enums or Python conditionals.
- Add recursive-CTE traversal with configurable depth/minimum weight, 2-hop
  neighborhood, and shortest-path queries in Postgres.
- Canonicalize symmetric pairs, count a pair at most once per scope/document,
  and exclude self-links.
- Implement the brief's degree, betweenness-centrality, and Louvain-community
  outputs in this milestone. Record correctness fixtures and a benchmark. If a
  graph library is proposed, its ADR must justify it against a straightforward
  Python baseline; the metrics are not silently deferred.
- Bake a small overview graph and per-entity neighborhoods instead of the full
  corpus graph in one file.

#### Acceptance criteria

- Every displayed edge opens at least one evidence pair.
- A 2-hop query and shortest-path query have deterministic tests.
- Golden typed-relation examples report precision/recall and every accepted
  relation identifies its configured relation type, confidence, extractor, and
  two supporting spans.
- Degree, betweenness, and community outputs match a hand-computed graph
  fixture, and runtime/peak memory are recorded for a real corpus snapshot.
- Retracted evidence reduces or retracts current links correctly.
- A graph fixture proves symmetric pairs are not double-counted and self-links
  never appear.

M6 uses document-level evidence because M8 deduplication does not exist yet.
When M8 lands, it must recompute corroboration-sensitive edge weights by story
cluster without changing the original M6 link evidence.

### M7 — Events and geography

#### User outcome

The user can ask what happened near a place and receive time-resolved events
whose people, places, coordinates, and reports all lead back to source spans.

#### Software-engineering work

- Define `Event` through ontology configuration with what, where, when, who,
  and reported-by roles; do not hardcode an OSINT event class in core code.
- Extract event roles and resolve absolute and relative dates against the
  source publication time. Preserve the original date phrase as evidence.
- Load and index the GeoNames dump, including Arabic alternate names, through a
  versioned gazetteer build.
- Resolve unambiguous places first, then use document context and their centroid
  to rank ambiguous candidates. Retain alternatives and scores rather than
  discarding ambiguity.
- Store coordinates in PostGIS and implement radius, bounding-box, and nearest-
  neighbor queries.

#### Acceptance criteria

- Relative-date golden cases cover month/year boundaries and missing
  publication dates.
- Ambiguous toponym cases show the chosen candidate, alternatives, features,
  gazetteer version, and exact source span.
- The brief's checkpoint query returns all evidenced events within 50 km of a
  point in the last 30 days.
- Retraction of the supporting document retracts the current event/location
  assertion without deleting its history.

### M8 — Story clustering, source coverage, and reliability

#### User outcome

Thirty syndicated copies become one stable story. The system separates URL
count, distinct-publisher coverage, and genuinely independent corroboration.

#### Software-engineering work

- Implement and measure a SimHash near-duplicate baseline for article text.
- Give stories durable UIDs separate from clustering generations. Record
  append-only `continued`, `merged_into`, and `split_into` lineage using article
  overlap, and publish redirects so saved story URLs survive re-clustering.
- Record every append-only cluster-membership decision with document UID, blob
  SHA-256, compared whole-document or bounded-span offsets, SimHash/similarity
  value, threshold, algorithm version, and decision provenance. A new cluster
  generation supersedes or retracts old membership; it never mutates it.
- Bake `stories/index.json` and one `stories/{uid}.json` per story with bilingual
  representative titles, timeline, configured facets, entities, and evidence.
- Report **distinct publisher coverage** until publisher ownership, shared wire
  origin, and known source derivation are modeled. Only then label the stricter
  measure **independent sources**.
- Implement the brief's separate Admiralty source-reliability (A–F) and
  information-credibility (1–6) axes. Each rating exposes its inputs,
  uncertainty, time window, and evidence; unknown data yields `unknown`, not a
  confident midpoint.
- Track source reliability over time against later corroboration or an
  adjudicated outcome set, and publish a calibration/failure report before
  using it to rank the main feed. Do not train and validate a source score on
  the same self-referential publisher-count signal.
- Recompute graph edge weights by story cluster so repeated wire copies do not
  masquerade as repeated corroboration; retain raw document evidence.

#### Acceptance criteria

- Known syndicated copies collapse into one story in the golden set, while
  different events sharing boilerplate stay separate at the chosen threshold.
- Duplicate-pair precision/recall and failure examples are recorded.
- A provenance query explains one membership from story UID through its
  versioned similarity decision to the exact document/blob inputs; retraction
  preserves the historical membership and removes it from the current story.
- Unchanged, merged, and split clusters have deterministic UID lineage; an old
  URL resolves to the current story or an explicit split choice.
- Publisher coverage is reproducible from the displayed membership, and the UI
  never calls it independent without the dependency model.
- A claim's Admiralty rating shows both axes and the supporting source groups.

### M9 — Temporal analysis and backend alerting

#### User outcome

The system explains what changed and evaluates durable entity, geofence, and
keyword watchlists when new evidence arrives.

#### Software-engineering work

- Build daily mention-count time series per stable entity and evidence-backed
  24-hour/7-day deltas.
- Implement the rolling z-score baseline with configurable window/threshold,
  then implement Kleinberg two-state burst detection and compare both on the
  same labeled/backtested periods.
- Persist versioned saved queries for entity IDs, M7 geofences, and keywords.
  Evaluate them on ingest through an inverted index rather than checking every
  watchlist against every document.
- Deduplicate alerts by underlying event/story and retain the exact documents,
  spans, algorithm version, and threshold that caused each alert.
- Generate deterministic change statements from those results; do not add a
  generic LLM summary.
- Browser local storage may mirror a user's selection as a UI convenience, but
  it is not a replacement for the M9 saved-query and alert pipeline.

#### Acceptance criteria

- Normal, sparse, and bursty fixtures compare z-score and Kleinberg outputs,
  with false alerts and missed known spikes reported.
- The brief's checkpoint passes: create a persisted watchlist, ingest a matching
  document, and observe one deduplicated alert with evidence.
- A benchmark demonstrates the inverted-index candidate reduction versus naive
  watchlists × documents evaluation.
- Stable entity/story lineage remaps saved queries or creates an explicit
  ambiguity; it never silently watches the wrong object.

### M10 — API/frontend contract and investigation export

#### User outcome

The user can query the complete product through the brief's API/frontend and
hand someone a compact, reproducible case file rather than a screenshot.

#### Software-engineering work

- Preserve M10's FastAPI/Pydantic API, cursor pagination, generated OpenAPI and
  TypeScript client, entity profile, graph, map, cross-filtering timeline,
  virtualized tables, provenance drawer, and SSE alert-feed requirements. The
  current static dashboard remains the production surface until this milestone.
- Export selected entities, stories, relationships, filters, evidence URLs,
  timestamps, and provenance references as JSON and Markdown.
- Include snapshot generation time and schema version.
- Never export source bodies by default.
- Make exports deterministic so the same snapshot and selection produce the
  same artifact.

#### Acceptance criteria

- Exported Markdown is readable without the dashboard.
- Every conclusion includes its source URLs and evidence identifiers.
- A JSON export validates against a published schema.
- Sensitive configuration and full article bodies never appear.
- The original M10 10k-node interaction checkpoint and the portable-export
  demo both pass; the export addition does not replace the frontend checkpoint.

## 7. Bilingual behavior for the UI agent

These are behavioral requirements, not styling instructions.

- Default mode is **English first**.
- An English article title is primary; `Original Arabic` is immediately below
  or beside it, not hidden several clicks away.
- An entity’s English/romanized name is primary; its exact Arabic canonical
  name remains visible and its aliases are expandable.
- Evidence shows translated and Arabic sentences as a pair.
- Every machine-translated value is labeled `Machine translated`; a
  transliteration is labeled `Machine transliterated`, and a verified alias is
  labeled separately.
- Translator name/version belongs in a details/provenance view.
- If English is unavailable, show Arabic plus `English translation unavailable`.
  Never copy Arabic into an English field to make the shape look complete.
- Use `lang="ar" dir="rtl"` only on Arabic content nodes. Use `<bdi>` around
  mixed-script names and metadata.
- Do not call the product real-time. Use `Scheduled snapshot` and show the
  actual generated time and expected refresh interval.

## 8. UI-agent acceptance criteria

- A first-time English-speaking user understands the system and latest signals
  without reading Arabic.
- Arabic original evidence remains visible for every translated claim.
- Search, filters, sort, result count, reset, and browser Back work together.
- Query and filter state are reflected in the URL.
- Review pairs show bilingual evidence and a plain-English model explanation.
- No untrusted external string reaches `innerHTML` unescaped.
- Keyboard access covers search, filters, language controls, disclosures, and
  review actions with visible focus.
- Charts have text/table equivalents; color is never the only escalation cue.
- The result meets WCAG 2.2 AA contrast and touch targets are at least 44px.
- Reduced motion is honored.
- At 375px there is no horizontal page scroll and RTL text does not reorder LTR
  metadata.
- Empty, stale, partially failed, and translation-missing states are designed
  explicitly rather than appearing as blank cards.
- Both the Cloudflare and portfolio deployments pass the same browser story.
- Portfolio project copy describes the deployed Neon/R2/Cloudflare pipeline,
  provenance, entity resolution, and review loop; it does not retain obsolete
  Streamlit, TF-IDF, or Docker claims after the corresponding code is gone.

## 9. Ownership boundary

### Software-engineering agent owns

- schema and migrations;
- stable identity and lineage;
- pipeline stages and data-quality invariants;
- translation/romanization/search inputs;
- evidence excerpt extraction and provenance;
- model sampling, training, evaluation, calibration, and rollback;
- link/relation generation, events, geography, graph metrics, story clustering,
  source measurement, time series, and alert evaluation;
- FastAPI/OpenAPI behavior, cursor semantics, and generated client contracts;
- baked JSON contracts, schemas, fixtures, and compatibility tests;
- security boundaries and production smoke tests; and
- an ADR and checkpoint for every real architecture decision.

### UI agent owns

- page and component structure;
- navigation and progressive disclosure;
- language-display controls;
- typography, spacing, visual hierarchy, charts, tables, and responsive layout;
- filter/search interaction state using the agreed contract;
- accessible rendering and keyboard behavior; and
- presenting loading, empty, stale, partial, and error states.

### Joint checkpoint before either side builds

For each slice, agree on:

1. the user story;
2. one public fixture;
3. the additive JSON contract;
4. empty/error/partial states;
5. measurable acceptance criteria; and
6. the rollback path.

The frontend must not invent intelligence that the pipeline does not produce.
The pipeline must not bake fields that have no defined user need.

## 10. What not to build yet

- **No generic AI chatbot.** It hides weak retrieval and creates uncited output.
- **No 3D globe or force graph as decoration.** Earn the visualization with
  stable entities, measured links, and inspectable evidence first.
- **No WebSockets or “live” badges.** The system is intentionally scheduled.
- **No Kafka, Kubernetes, Neo4j, or microservices.** Current scale does not
  justify them.
- **No account system or multi-tenancy before M11.** M9 still persists and
  evaluates watchlists through the single-owner backend/CLI; local selection
  and shareable URLs are conveniences, not substitutes for alert evaluation.
- **No black-box credibility score.** Deduplicate stories and measure
  independent corroboration before rating credibility.
- **No generic sentiment map.** It is not a substitute for evaluated event or
  escalation extraction.
- **No more charts just to fill space.** Every visualization must answer a
  specific investigation question and have a text equivalent.
- **No claim that the ML works in production until model-originated lift is
  measured.**

## 11. Delivery checkpoints

First checkpoint: George approves or edits the milestone amendment in section
5. After that, every row below is its own explain → ADR → build → demo → stop
cycle. Do not combine rows just because they touch the same files.

| Slice | Independently demoable result | Rollback boundary |
|---|---|---|
| F0.1a | Retracted/current-generation fixtures produce the exact documented distinct-document count | Revert aggregate query only |
| F0.1b | Forced replacement failure leaves no new rows and preserves the prior live generation; successful re-extraction leaves one live generation | Revert transaction/version switch |
| F0.2a | Empty and prior schemas migrate through an expand revision while old code still runs | Restore DB backup or forward repair before app deploy |
| F0.2b | Idempotent backfill resumes after interruption; contract refuses to run before both consumer pins | Keep expanded nullable fields; do not contract |
| F0.3a | Canonical schema/fixture bundle fails both consumers on a breaking field change | Pin previous immutable contract bundle |
| F0.3b | Script/URL fixtures render safely and split assets pass deliberate CORS tests | Redeploy prior static consumer version |
| F0.4a | Newer data publishes; stale data is rejected; rollback creates a newer promotion of an old payload without lowering the data high-water mark | Create another audited promotion of an immutable payload |
| F0.4b | Broken source and killed-run fixtures produce failed/abandoned ledger events and honest health | Omit health projection while preserving audit events |
| M4.1a | Frozen scorer audit plus separate pre-blocking gold set, labeling guide, and uncertainty-aware baseline report exist | Keep current scorer artifact |
| M4.1b | Candidate scorer either passes the precision gate with model-originated lift or is visibly rejected | Repoint scorer-version config to prior artifact |
| M4.2a | Old constraints remap across an extractor bump; shifted/conflicting evidence enters review | Disable new remapper, retain append-only decisions |
| M4.2b | Stable entity UID survives recomputes and has deterministic merge/split lineage | Keep generation IDs public until lineage is approved |
| M5.1a | Versioned translation/transliteration records show value kind and degrade without DeepL | Disable provider, retain Arabic and romanization |
| M5.1b | Fifty-query bilingual evaluation and split search artifact find the same UID across scripts | Keep additive old fields and previous index version |
| M5.2 | One bounded bilingual entity evidence file proves mention, sentence, and translation provenance and passes disclosure tests | Stop publishing detailed file; main snapshot remains compatible |
| M5.3 | One review decision uses bilingual evidence and survives the next resolver generation | Fall back to existing review fixture/contract |
| M6.1 | Evidenced co-occurrence links, 2-hop traversal, and shortest path pass a hand-built graph | Retract the link generation, not source mentions |
| M6.2 | Typed relations plus degree/betweenness/Louvain outputs pass golden and benchmark checks | Keep M6.1 links; retract failed typed/metric generation |
| M7.1 | Relative-date event extraction passes boundary cases with exact source evidence | Retract event extractor version |
| M7.2 | Ambiguous places are explainable and the 50 km/30-day PostGIS query passes | Repoint to prior gazetteer/extractor version |
| M8.1 | SimHash golden set, membership provenance, and stable story merge/split redirects pass | Retract cluster generation; retain documents |
| M8.2 | Publisher/dependency groups and separate Admiralty axes are reproducible and calibrated | Show coverage only; suppress rating |
| M9.1 | Z-score and Kleinberg backtest compares false/missed bursts | Keep time series; suppress burst labels |
| M9.2 | Persisted watchlist fires one deduplicated evidenced alert through the inverted index | Disable evaluation job; preserve saved query |
| M10.1 | Cursor API, OpenAPI, and generated client pass contract tests | Keep current static consumer live |
| M10.2 | Original 10k-node graph/timeline/map checkpoint passes | Keep API and static dashboard; do not promote frontend |
| M10.3 | Deterministic JSON/Markdown case file validates and opens without the app | Remove additive export action |

M11 then proceeds exactly as written in the brief, after M10 is accepted.

## 12. Definition of done for every feature

A feature is done only when:

- its user problem is written in one sentence;
- George can explain the algorithm and Python used;
- domain and language behavior remain configurable;
- derived facts retain source-span and extractor-version provenance;
- append-only/retraction semantics are preserved;
- public data contains no source bodies or secrets unless explicitly approved;
- unit, integration, contract, and real-output checks pass in proportion to
  risk;
- one failure example is documented alongside success metrics;
- an ADR records any real decision;
- both public consumers remain compatible; and
- the checkpoint has a runnable demo and a rollback path.

## 13. The 90-second hiring-manager demo this roadmap should enable

1. Open the overview and explain that it is a scheduled, zero-idle-cost Arabic
   intelligence pipeline, not a mocked dashboard.
2. Read a concise English “what changed” signal and reveal its Arabic original.
3. Search an English spelling of an Arabic name and open the same stable entity
   found by its Arabic spelling.
4. Show the entity’s bilingual timeline and exact source-span provenance.
5. Open a story cluster and distinguish duplicate coverage from independent
   corroboration.
6. Trace a relationship path and open the evidence for one edge.
7. Show a human resolution decision, then show the measured model report that
   used production-shaped labels.
8. End on system health: source status, freshness, versions, and the fact that
   Cloudflare and Vercel consume the same tested contract.

That demonstration shows product judgment, data modeling, algorithms, ML
evaluation, internationalization, security, testing, cost-aware deployment,
and honest production operations without adding technology for its own sake.
