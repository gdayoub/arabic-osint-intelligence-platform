# 0033: Add a public intelligence atlas without changing dashboard v1

## Context

The public dashboard showed country totals but exposed only 50 articles per
country. Its archive could not support those totals, and the portfolio consumes
the existing dashboard v1 JSON contract. George requested a useful, visually
strong demo centered on a risk-colored 3D globe.

## Options considered

1. Replace the existing contract and port the application to React. This would
   couple the portfolio migration to the redesign and add a build system.
2. Aggregate the existing 50-article country exports as a complete archive.
   This would misrepresent coverage and lose unassigned articles.
3. Keep the static application and publish an additive, validated archive.

## Decision

Use option 3. `countries/_atlas.json` includes every currently processed live
article's public metadata and current entity memberships. It is validated and
staged alongside country files using the same generation timestamp. Dashboard
v1 and its frozen fixtures remain unchanged. No database migration is needed.

The browser uses plain ES modules. Globe.GL handles spherical geometry,
selection, camera controls, and WebGL rendering; hand-writing that renderer
would obscure the research features. It is pinned and vendored (2.46.2,
approximately 1.89 MB raw), with a keyboard-accessible country list fallback.
Natural Earth provides coarse public-domain boundaries.

Color represents high-escalation articles divided by classified articles under
the current filters. Counts and publisher coverage accompany the percentage.
Unknown classification is excluded from the denominator; no reporting is gray.
This is a reporting signal, not an assessment of personal safety. Approximate
Gaza/Bahrain navigation markers do not claim precise territorial boundaries.

The original reviewer console remains at engine.html. Country and high-risk
URLs enter the same new interface with appropriate filters. The deployment and
review workflows both include the new assets, preventing a later review publish
from removing the interface.

## Consequences

The first updated pipeline run provides the complete archive. Until then,
legacy country exports are explicitly labeled as an incomplete public sample.
A frozen example is an explicit demo choice. Publication dates drive filters;
collection dates are shown separately. No article bodies or fabricated scoring
excerpts are exported.

Entity IDs are valid only within a resolver snapshot. Shared views search names
instead of promising permanent entity identities. Research notes stay in browser
storage and can be exported as JSON or a printable sourced HTML briefing.

This release adds globe/list exploration, country comparison, filtered archive,
publication timeline, entity dossiers, evidence details, local research board,
view links, and a guided introduction. Relationship graphs, historical snapshot
diffs, clustering, and alerts remain later milestones, requiring their own data
semantics and validation.

## Reading the Python

`bake_atlas` first selects live documents and current mentions. Its dictionaries
index records by ID, so joining membership records does not require repeatedly
scanning every document. `defaultdict(set)` creates an empty set when a document
first receives an entity; the set deduplicates repeated mentions. Sorted IDs
produce reproducible output. Pydantic's `model_validator` checks the whole
snapshot after individual fields have been checked, rejecting orphan entity
references and duplicate identities before publication.

Run `python scripts/preview_atlas.py`, then serve its `preview` directory with
`python -m http.server 8765 --bind 127.0.0.1 --directory preview`.
