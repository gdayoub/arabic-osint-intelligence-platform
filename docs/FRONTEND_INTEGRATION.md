# Frontend integration checkpoint

This backend batch targets OSINT `main` at
`3a9bd6978fb57573ad323b8f6dcb81ccc8492922`. It is a separate draft for review.
The current console on `main` is the integration baseline. These fixed
contracts are the target for the separate frontend implementation. No unknown
frontend branch needs to be found or reconciled first. End-to-end compatibility
still needs verification when the consumer is implemented.

Start with [the frontend build contract](frontend-handoff/FRONTEND_AGENT_PROMPT.md)
and its adjacent `frontend-contract/` bundle. The bundle pins the backend code
revision, carries synthetic public/evidence examples, and includes a read-only
validator. It does not enable fixtures or private evidence in production.

## Existing dashboard consumers

The dashboard v1 producer model, generated schema/types/fixtures, contract lock,
and pinned portfolio consumer lock are unchanged. The static HTML pages,
workflows, dependencies, migrations, and live scorer weights are also unchanged.
`python scripts/generate_dashboard_contract.py --check` verifies the committed
v1 bundle. Existing bake and contract tests exercise the default v1-only path.

The [portfolio consumer lock](../contracts/dashboard/consumers/george-portfolio.lock.json)
still names commit `203a63b38f53bccfe28e0e53e02e4d0a2c603c4c` and its two
required consumers, `app/api/osint/route.ts` and `public/osint-dashboard.html`.
That is a pinned compatibility target, not a claim that it is the current
frontend implementation. This draft does not change or build that repository.
The consumer workflow has path filters and will not run automatically for this
batch; a green backend test workflow alone does not verify the portfolio build.

## Public metadata archive

- Artifact: `countries/_atlas.json`, [schema](../contracts/atlas/1.0.0/atlas.schema.json),
  [synthetic fixture](../contracts/atlas/1.0.0/atlas.fixture.json),
  [scope and local export instructions](../contracts/atlas/README.md).
- It is opt-in through `run_bake(..., include_atlas=True)` or
  `python -m scripts.bake_atlas`. No default CLI/runtime/workflow activates it.
- `complete: true` means every **currently processed, non-retracted document**
  in one read snapshot, including undated and country-unassigned rows. It does
  not mean all historical or unprocessed records.
- Entity IDs are snapshot-local. A consumer must not persist them as stable
  person/profile identities across resolver generations.
- The archive carries titles, URLs, current memberships, aliases, and existing
  cached translations or null. It carries no bodies or evidence excerpts.
- CORS adds only `/countries/_atlas.json` alongside the existing `/data.json`.
  Country JSON routes, private evidence, and other paths do not gain CORS.
- A later default v1-only bake replaces the country directory and removes an
  older local archive. Enable and release the producer/consumer together in a
  separately verified activation; do not rely on a stale archive lingering.

## Private bilingual evidence

[The offline interface and runnable examples](BILINGUAL_EVIDENCE_HANDOFF.md)
operate on a caller-supplied private snapshot. The [synthetic input](../tests/fixtures/bilingual_evidence.json)
contains two documents and no cached translation. It exercises Arabic/Latin
search and exact original source spans; it is not production news evidence.

Keep original names, romanizations, attested aliases, and cached translations
separate in any future UI. Missing translation remains null. Spans use Python
Unicode code-point offsets; JavaScript UTF-16 indices need explicit conversion.
The result's hashes and source window must stay paired with the original text.

The public archive cannot serve as this reader's input: it deliberately omits
bodies, exact spans, and private translation provenance. Connecting an Evidence
Lab or a live UI still needs a coherent authorized snapshot adapter, source
access controls, excerpt/publication permissions, safe rendering, and paired
Arabic/translated display. This draft provides no live endpoint or UI wiring.

## Known parallel work to reconcile

Checked on 2026-10-12:

- [OSINT draft #37](https://github.com/gdayoub/arabic-osint-intelligence-platform/pull/37),
  `feat/intelligence-atlas-ui` at `393b1cd7a47b6130f7ec15439e6784908ca232b4`,
  has overlapping archive/bake/runtime/worker code, tests, benchmark notes, and
  independently numbered ADRs 0033/0034. Do not combine exporter versions or
  activation choices mechanically. Resolve duplicate ADR numbering as well.
- The OSINT `worktree-intelligence-atlas` head remains
  `a3ab76966a16f6a201cb48f7b4f5cd320592665a`.
- Portfolio `main` remains `987e0473078073f7862c704aedc67f1a64080ab5` and
  `codex/osint-observatory-release` remains
  `9577ac996565a51a793ea8696dd64188f46aeb14`.

These are historical parallel implementations, not the current frontend task.
No changes from them have been merged by this draft. They matter only if their
UI or activation code is deliberately reused.

## Before marking ready

1. Build from the current console using the fixed public contract and the
   separately private evidence interface described above.
2. If reusing parallel atlas work, reconcile overlapping contracts and same-file
   changes rather than importing an older exporter or activating it implicitly.
3. Test the actual consumer against v1 fixtures plus the opt-in archive fixture;
   run its build and browser flows for loading, empty/missing/stale data,
   search, navigation, mobile, and source attribution.
4. Verify PostgreSQL archive snapshot isolation and coherent release collection.
5. Review private evidence access/excerpt permissions separately from public
   metadata. Keep activation and deployment as explicit later decisions.
