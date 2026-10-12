# OSINT frontend build contract

Version 2, frozen October 12, 2026. This replaces the earlier expanding frontend roadmap and its three release milestones. Implement the single slice below now in the existing portfolio console. Start from the current console; an unidentified branch, an unpublished desktop checkout, or another planning round is not a prerequisite.

## Build now

Turn the existing black/red OSINT console into one coherent, evidence-led analyst workspace: overview, filterable reporting explorer, metadata detail drawer, entity browser, human review, and honest data status. Preserve the original project, route, data pipeline, and portfolio widget. Build the archive and bilingual evidence components against the supplied contract-valid synthetic fixtures immediately, while keeping their unsupported production capabilities off.

There is one product and one frontend. The fixture harness exercises the same components; it is not a second app or a replacement Evidence Lab. Deliver working code, tests, and a local visual preview. Do not merge, deploy, change the public data contract, activate archive publication, or create new backend endpoints under this task.

### Pinned inputs

- Frontend: [gdayoub/george-portfolio](https://github.com/gdayoub/george-portfolio/tree/987e0473078073f7862c704aedc67f1a64080ab5), baseline `987e0473078073f7862c704aedc67f1a64080ab5`.
- Actual entrypoint: `public/osint-dashboard.html`. It is standalone HTML/CSS/JavaScript, with Inter, JetBrains Mono, and Chart.js 4.4.7. The portfolio being Next.js does not make this page a React app.
- Preserve `app/api/osint/route.ts`, `lib/osint-contract.ts`, `components/OSINTWidget.tsx`, and the contract lock. `/api/osint` is a five-article widget projection, not the full-console API.
- Backend implementation: [draft PR 38](https://github.com/gdayoub/arabic-osint-intelligence-platform/pull/38), branch `codex/osint-backend-foundations-2026-10-12`, code SHA `8d7e241a8073a706900ea81f744a5a879cbe9132`. This is a code pin, not a deployment claim. Later handoff-only commits may follow it.
- Bundle beside this prompt: `frontend-contract/`. `types/dashboard.types.ts` and all JSON Schemas are unchanged copies from that backend. Atlas/evidence TypeScript files are consumer mirrors; runtime validation still matters.

Read the checkout instructions and relevant `.agents/skills`, inspect the current Git status, and preserve unrelated edits. If the baseline has advanced, compare it rather than resetting it. Capture the original page, then start a small vertical slice: shell + real v1 adapter + explorer + metadata drawer. Explain the approach briefly and show the first usable desktop/mobile preview; do not turn that into another open-ended planning dependency.

## Fixed scope and capability gates

1. **Live dashboard v1:** improve the original console using the existing public snapshot. Add shared recent-feed filters, source/country selection, collection/publication date controls, article detail, refresh/error/freshness handling, and safe rendering. Keep all existing overview/entity/review/source-navigation functionality.
2. **Complete-current archive explorer:** implement client filtering, article/entity joins, dictionary search, counts, and detail against the supplied synthetic archive. The real archive adapter is implemented behind a disabled activation flag. No live archive request or “complete archive” claim until a real coherent artifact is published and verified.
3. **Bilingual evidence drawer:** implement its exact rendering and state handling in the developer preview using the eight precomputed reader outputs. Production metadata drawers state that source excerpts are unavailable. There is no live evidence search, document-body fetch, or private-data upload.
4. **Local metadata export:** download the currently filtered loaded rows as JSON, including source URLs, mode, filters, generation identity, scope, and count. Label synthetic exports as synthetic. This is a browser-local convenience, not a saved investigation service or an archival evidence packet.
5. **Methodology and coverage:** explain what is counted and what is missing. Show derived coverage for the loaded population. Backend evaluation reports and pipeline phase visibility do not create public quality/health APIs.

Not in this slice: graph or relationship claims, geolocated incidents, a 3D globe/new map dependency, watchlists or alerts, source-credibility scores, independent corroboration, historical snapshot comparison, authenticated cases/notes, real-time streams, model training, translation generation, stable person profiles, or production excerpt publication. Do not add dead navigation for these.

## Actual transports and contracts

### Live public data

Only the existing public dashboard is enabled by default:

`https://arabic-osint-dashboard.georgedayoub500.workers.dev/data.json`

This is a periodically baked snapshot, not a streaming API. Read `DashboardSnapshot` from `frontend-contract/types/dashboard.types.ts`; producer authority is `src/contracts/dashboard.py` and `contracts/dashboard/1.0.0/` in the pinned backend.

- `stats.total_raw` and `stats.sources` describe live collected documents, including unprocessed records.
- `stats.total_processed`, topic/escalation distributions, and `countries` describe current processed documents.
- `recent` is a bounded sample and can contain unprocessed rows; do not hard-code its length or give these rows invented IDs/memberships.
- `daily` is a bounded collection-day series. It is not event time, publication time, or proof a collector ran successfully.
- `processed_at` on v1 articles means **Collected**. Preserve nulls.
- Entity ranking `count` means supporting mention count, not article count. `entities.total` means entities; `mentions.total` means mentions. The top lists are bounded and have no entity IDs.
- Preserve original/available English titles. `title_en` is cached machine-translated title data; it is not an evidence translation. `ai_summary` is currently not usable for a generated briefing.

Existing external navigation remains valid as a link, with encoded trusted parameters:

- `https://arabic-osint-dashboard.georgedayoub500.workers.dev/country.html?c=<slug from countries[]>`
- `https://arabic-osint-dashboard.georgedayoub500.workers.dev/high_risk.html?level=high` (also medium/low)

Do not fetch country JSON from the portfolio browser: this batch grants it no CORS. Do not stitch capped country files into an allegedly complete archive. Preserve separate “Open country page” / classification drilldown links even when adding in-page filters.

### Archive data for local preview and later activation

Actual opt-in producer: `scripts/bake_atlas.py`; Python contract `src/contracts/atlas.py`; schema `contracts/atlas/1.0.0/atlas.schema.json`. The staged path is `countries/_atlas.json`. The Worker patch adds CORS only for that exact path, in addition to `/data.json`. This path is **not verified published** and the default pipeline still does not bake it.

Read `AtlasSnapshot`, `AtlasArticle`, and `AtlasEntity` from `frontend-contract/types/atlas.types.ts`. Important exact distinctions:

- `atlas_version: 1`, `complete: true`, `scope: "current_processed_live_documents"`, `entity_id_scope: "snapshot_local"`, `translation_scope: "cached_machine_translation"`.
- `snapshot_id` is `sha256:<64 hex characters>`, derived from the complete public payload excluding itself. It identifies this metadata snapshot, not a person or a historical corpus.
- Articles have string `id`, string `entity_ids[]`, nullable `url`, `title_en`, `country`, `published_date`, and `collected_at`. `topic` and `escalation` are nonempty strings, not enums and not nullable in this contract.
- Entities have `id`, `name`, nullable `name_en`, `object_type`, nonempty `surface_forms[]`, and `romanizations[]`. Surface forms are observed forms, not verified aliases. Romanization is not translation.
- There are no bodies, evidence windows, coordinates, review actions, stable IDs, pipeline health, or query endpoint in this archive.

Require runtime shape/version checks, unique IDs, valid entity references, no orphan entities, safe URLs, valid timestamps, and coherent dashboard/archive generation. A real activation must also reconcile `articles.length === dashboard.stats.total_processed`, processed topic/escalation/country totals, and sample membership as implemented in `validate_atlas_bundle`. Never combine mismatching snapshots. Re-fetch a candidate coherent pair at most once; retain the last coherent pair or recent-v1 mode with a clear warning. Do not overwrite the displayed usable state with a broken partial load.

Browser shape checks cannot prove source completeness or authenticate a payload. Do not display “cryptographically verified” merely because `snapshot_id` exists. If implementing the Python hash algorithm in JavaScript, test canonical UTF-8 serialization against these fixtures; ordinary `JSON.stringify` ordering is not that algorithm.

### Private offline evidence

Actual Python calls, usable locally without a server:

```python
from pathlib import Path
from src.search.evidence_cli import load_snapshot
from src.search.evidence import search_evidence
corpus = load_snapshot(Path("tests/fixtures/bilingual_evidence.json"))
result = search_evidence(corpus, "Mohammed al-Ahmad")
```

`frontend-contract/types/evidence.types.ts` describes the actual returned JSON. It has `schema_version: "offline-evidence-v1"`, reader version, `data_kind`, `access_scope: "private_local_only"`, `scope: "caller_supplied_snapshot"`, limits, match counts, results, typed names, exact source windows, hashes, extractor/segmenter identity, translation status, and coverage.

It has **no public transport**, no title/date fields on evidence windows, and no durable snapshot ID. Do not invent `/api/search`, `/api/evidence`, `/health.json`, `/evaluations`, or similar routes. Do not join its numeric entity/document IDs to the archive's string IDs. The supplied archive and evidence fixtures are separate corpora.

## File and adapter boundary

Keep implementation in the existing project. Suggested new assets are `public/assets/osint/{console.css,console.mjs,adapters.mjs,model.mjs,render.mjs}`; equivalent small modules are fine. Keep tests and fixtures under `tests/osint/` outside `public/`. The existing page loads the modules. No framework migration or separate application scaffolding.

Use type-only/JSDoc imports from the supplied `types/` files. `adapter-contract.types.ts` defines **new frontend-local** interfaces to implement, not endpoints:

- `ConsoleDataAdapter.mode`: `live_public` or `synthetic_preview`.
- `loadDashboard(signal)` exists in both adapters.
- `loadArchive(signal)` is absent when the live activation flag is off; supplied by the local preview adapter.
- `readEvidenceExample(case)` exists only in the local preview adapter. The case selector loads a precomputed example; it must not masquerade as arbitrary live server search.

Keep fetch/validation separate from normalization, selectors, and DOM rendering. Keep raw metadata immutable. Use pure selectors to derive all filtered rows, counts, facets, and timeline bins. Preserve unknown strings instead of forcing them into a familiar enum. Supply abort signals or request generations so late refresh responses cannot replace newer state.

**Default production gate:** live v1 adapter, archive disabled, offline evidence absent, no fixture request, no fixture switch in the public UI. A query parameter alone must not enable fixtures in production. Use a localhost-only developer harness or explicitly nonproduction build configuration, with fixture assets excluded from production output. The harness loads the same render/model modules and displays a persistent “Synthetic local preview — not live intelligence” banner. Exports and screenshots retain the mode label. Refresh failure never falls back to fixtures.

Older unpublished native work at `eaae920411fb4d7e4e61a66669881540ec534c38` may later donate country/date/timeline/export functions and tests. Do not wait for it or claim it is in this baseline. Isolate state/model/adapter code now so those functions can be compared and ported later, rather than wholesale replacing the page or merging two inline applications. Record touched files and decisions in the frontend handoff.

## Screens and interactions

### Overview

Use a compact sticky header and restrained section rail/tabs: Overview, Explore, Entities, Review, Methodology. Show source mode, generated-at time in UTC, age, fetch state, and Refresh. Reduce the large marketing hero and ornamental motion. Retain the black/charcoal base, red identity accent, crisp borders, Inter labels, monospaced numbers, and excellent Arabic typography. Professional analyst-software influence means coordinated panels and legible evidence, not military theatre or copied Palantir assets.

Show collected/processed counts, represented sources, and high-classification count with explicit denominators. Keep global overview cards global when explorer filters change. Derive top country from `countries[]`, not `recent[]`; derive top topic by count, not array position. Statistical briefing only: no invented AI narrative. Country coverage is a sortable accessible list/bar view, showing reporting volume and unassigned coverage. A country value is a text classification, not incident coordinates or a geopolitical finding.

### Explore

One toolbar, active filter chips, loaded-result count, date histogram, article list/table, and shared right-side detail drawer. At narrow widths, use cards, a filter sheet, and a full-height detail panel. Preserve search and English-title toggle across filtering and selection.

- Production starts as “Recent sample: N loaded.” Preview can select “Complete current processed archive: N loaded.” The archive label never says complete history.
- Filters: query; country plus unassigned; source; topic plus unclassified; escalation plus unclassified; archive entity; date basis (Published / Collected); date mode (All dates / Date range / Undated only). Offer entity filtering only for archive rows.
- Clicking a country/source/classification in overview adds the corresponding explorer filter and takes the user to Explore; overview totals stay labeled global. Distinguish the separate external-page action from this local filter action.
- Selecting a row opens its drawer without losing filters or scroll. Show original title, optional labeled English title, source link, classification, country, publication and collection times, loaded scope, and archive memberships when present. Null URL displays “Source link unavailable” with no anchor. No invented summary or body.
- In production, the drawer's evidence area says “Source excerpts are not available in the public snapshot.” In the developer harness, a separate “Offline evidence examples” control exercises the same evidence component. Do not pretend an unrelated example belongs to the selected archive article.
- Export only the current filtered dataset, with original source URLs and scope. Do not export hidden private bodies, claim a frozen historical investigation, or persist numeric entity IDs as person identities.

### Entities and review

Live-v1 Entities preserves the top entity/mention rankings with their correct units and fallback labels. Since v1 supplies no entity/article join, its entity rows must not claim to filter articles by true membership. In archive preview, browse/search `name`, `name_en`, `surface_forms`, and `romanizations`; selecting an entity filters actual `entity_ids`. Display original, cached translation, observed surface form, and transliteration distinctly.

Review retains candidate identity, object type, source-linked left/right mention evidence, score, threshold, distance, and feature values. Score is a resolver score, not a calibrated probability. The existing actions open prefilled GitHub issues at `gdayoub/arabic-osint-intelligence-platform`; label them “Open same-entity review” / “Open different-entity review.” Opening an issue is not recording/applying a decision. Do not post issues automatically or show a success toast. Disable outbound review actions for synthetic fixtures.

### Bilingual evidence component

Render original Arabic and optional translation side by side, stacked on mobile. Show typed matched-name reason, source link, mention, extractor/segmenter versions, and expandable exact provenance. A `verified_alias` in synthetic data remains a fixture attestation; it is not an independently verified real-world identity.

Highlight with Unicode code points: `Array.from(window.text).slice(mention.start - window.start, mention.end - window.start).join('')`. Check bounds and equality with `mention.text` before highlighting. Never normalize original text or use raw JavaScript UTF-16 offsets on it; the emoji fixture is mandatory.

`translation_status` is authoritative: unavailable window, output over limit, fixture-only, cached-empty, or cached. Null/empty is not successful English evidence. Show cropped windows as cropped, and show omitted-window/oversized-mention flags. `no_bounded_live_evidence` does not mean no mentions. Never synthesize translations. Preserve provenance paired with its text.

## Exact filter and state semantics

The supplied `ExplorerFilters` type names the state. Across dimensions combine with AND; selections within one dimension combine with OR. An empty selection means no restriction. Country/unclassified toggles add the null bucket to that dimension; a null bucket alone selects only nulls. An entity selection in archive matches any selected `entity_id`.

Query is one trimmed, case-insensitive substring over individual available title/source/topic/country strings and, in archive mode, each member entity's supplied name/translation/form/romanization. Use Unicode NFKC, lowercase, and collapsed whitespace for comparison only. Do not concatenate fields to create cross-field matches, fuzzy-match identities, or generate new transliterations. Empty query matches all. This simple browser metadata search is distinct from the Python offline evidence matcher.

Date range is inclusive by UTC calendar date on the selected basis. Reject reversed/invalid ranges visibly. All dates includes null dates; date range excludes them and states how many are excluded; Undated only selects null dates for that basis. Never substitute collection time for publication time. Sort on the chosen basis; nulls last in both directions, deterministic ID/source-order tie breaking. Show histogram and facets for the same filtered population, including an explicit undated count. Do not populate days with zero when the dataset provides no observation coverage.

Reset clears filters/date range/selection, preserves source mode and English visibility, and restores the entire loaded dataset. Switching dataset clears unsupported entity IDs and closes stale details. If using URL state, encode only nonprivate filters, validate it, scope archive IDs by `snapshot_id`, and make Back/Forward restore coherent state. No private excerpts in URLs, storage, analytics, or console logs.

State coverage is required:

- Initial loading: labeled skeletons, no zero-valued facts.
- Empty valid snapshot: explicit empty state; no NaN/Infinity or fabricated zero-percent quality.
- No filtered matches: show applied filters and a clear reset action.
- Network/HTTP/schema failure: retry; keep last successful data visibly dated if present.
- Archive disabled/missing/mismatched: keep recent-v1 available; never silently relabel it as archive.
- Stale snapshot: display exact age. For this six-hour schedule, use a documented UI warning after 12 hours; this is a freshness heuristic, not proof of pipeline failure. Future/invalid time gets its own warning, not “fresh.”
- Missing topic/escalation: Unclassified; unfamiliar nonempty label: neutral style + original label. Null is never low. Classification labels are lexical/classifier outputs, not verified threats.
- Coverage: for each displayed population show title translation available/denominator, dated/undated by basis, assigned/unassigned country, and recognized/unclassified classification where relevant. Ratios with zero denominator read “Not available.” Bounded samples cannot establish corpus-wide coverage.

## Acceptance and verification

The bundle was generated from and checked against the actual backend validators, rather than hand-guessed JSON. It contains six archive articles, three entities, two country-unassigned rows, two publication-undated rows, two collection-undated rows, four recent rows including a wholly unclassified row, one review pair, hostile HTML-like text, non-BMP emoji, unknown labels, null links, and missing translations. Empty bundles and eight offline evidence outputs cover additional states. See `fixture-provenance.json` for exact expected counts.

1. Contract tests load both nonempty and empty dashboard/archive bundles; incorrect versions, IDs, references, dates, URLs, and generation mismatch fail visibly. Fixtures never alter the original v1 contract lock or widget projection.
2. Model tests prove filter AND/OR behavior, null buckets, inclusive dates, undated exclusion, reset, language-toggle persistence, membership joins, global-versus-loaded totals, deterministic sort, and no-match behavior.
3. DOM/security tests prove hostile fixture strings render as text, dangerous URLs cannot become anchors, bidi is isolated, and synthetic review actions cannot open real mutation flows. Use `textContent`/DOM construction; never interpolate untrusted data into HTML, styles, or handlers.
4. Evidence tests cover all eight files, exact diacritic/emoji highlighting, translation labels, truncated windows, empty results, and coverage caps. Fixtures with IDs that happen to match across corpora must still not join.
5. Browser flows at desktop and 390px cover search, country/source filters, dates, clear, detail/Escape/focus return, refresh twice, stale/failed/mismatched load, Back/Forward if implemented, keyboard navigation, and reduced motion. Provide screenshots of actual tested UI, labeled by data mode.
6. Accessibility: semantic headings, named inputs, focus-visible controls, non-color statuses, readable contrast, chart/list alternatives, keyboard drawer controls, status announcements, and no horizontal page overflow. Destroy/update Chart.js instances on re-render rather than stacking canvases/listeners.
7. Run the repository's `npm run test:osint-contract`, added focused model/DOM/browser tests, TypeScript checks where applicable, and `npm run build`. Baseline `npm test` is an intentional failing placeholder; `npm run lint` invokes `next lint` despite Next 16, so inspect the supported lint setup and report a script/tooling failure accurately instead of calling it a pass.
8. Confirm a normal production build never requests fixture files, enables offline evidence, or requests `_atlas.json`. Live source failures remain failures. Browser-test actual `/data.json` access and source navigation from the frontend origin; report any unverified checks.

For fixture verification in a backend checkout with normal dependencies:

```bash
python docs/frontend-handoff/frontend-contract/validate_bundle.py --backend .
```

When this handoff is outside that repo, use its local script path with `--backend /path/to/arabic-osint-intelligence-platform`. The supplied verification report covers data contracts only, not a frontend build or current production behavior.

Finish with changed files/commit, exact commands and results, desktop/mobile screenshots, live-versus-preview capability list, known limits, and rollback instructions. Mark the work **implemented and locally previewed**, not deployed. Stop at this completed frontend slice; later archive publication and private evidence transport are separate decisions.
