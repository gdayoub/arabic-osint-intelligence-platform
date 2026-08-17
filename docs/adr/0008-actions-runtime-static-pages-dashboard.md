# ADR 0008: GitHub Actions as the pipeline runtime; a static, baked Pages dashboard

## Context

The platform was hosted on Railway, which has no free tier. The goal is a
setup that runs indefinitely on free infrastructure with no manual
intervention. Two things need a home: the recurring scrape/classify
pipeline, and the public dashboard.

## Options considered

**Pipeline runtime:**
1. An always-on container (what Railway was) on some other free-tier host.
2. **GitHub Actions on a cron schedule — chosen.** Free, unlimited minutes
   on a public repo, no container to keep alive or pay for.

**Dashboard:**
1. Keep a live backend (FastAPI, `src/api/main.py`) serving `/api/*` on
   some free host, dashboard fetches it in real time.
2. **A static page fed by a JSON file the pipeline bakes after each
   run — chosen.** `scripts/bake_dashboard_data.py` writes `data.json`;
   `src/api/static/dashboard.html` fetches that file instead of `/api/*`;
   both are deployed to Cloudflare Pages by the same Actions run.

## Decision

GitHub Actions + static Pages, both options 2.

## Consequences

- **No always-on process anywhere in this stack.** Nothing to keep alive,
  nothing that can idle-timeout or cost money for sitting unused.
- **Accepted tradeoff, stated plainly: dashboard data is only as fresh as
  the last pipeline run** (every 6 hours, best-effort — GitHub's cron
  scheduler is not exact and can drift under load). This is a real
  limitation, not hidden behind a live-looking "Online" indicator — the
  dashboard shows `generated_at` from the baked file instead
  (`dashboard.html`'s `init()`), so staleness is honest rather than implied.
- **`data.json` must never contain document body text** — it's served from
  a public Pages deployment. Enforced structurally: `bake_dashboard_data.py`
  never touches the blob store or `documents.text` at all, only facts and
  document metadata columns. Backed by a regression test
  (`tests/unit/test_bake.py::test_bake_never_includes_document_body_text`).
- **Schema DDL (`init-core-db`) runs on every scheduled invocation.** This
  looks alarming at first — "never run migrations on a cron" is generally
  right — but `Base.metadata.create_all()` only creates tables that don't
  exist yet; it never alters an existing one. Since there's no backfill (the
  Railway data was not migrated — see the decision to abandon it) and no
  hand-rolled migration tooling was built, this is the actual mechanism by
  which the very first run creates the schema on an empty Neon database.
  **This is a real corner cut, not a considered long-term design**: the
  moment a real schema change is needed after the first run, `create_all()`
  can't express it (it doesn't `ALTER TABLE`), and something more deliberate
  is needed then — most likely the hand-rolled SQL migration approach
  described in the original M1.5 plan, introduced at that point rather than
  now.
- **Scheduled workflows auto-disable after 60 days of repository
  inactivity** — a real failure mode with no error notification. Mitigated
  by documenting `workflow_dispatch` as the manual recovery in the README
  (a repo push resets the clock; committing to the repo periodically is a
  cheap way to keep this from silently lapsing).
- **The old Railway-triggering workflow is disabled, not deleted**
  (`.github/workflows/scheduled_scrape.yml` — cron removed, `workflow_dispatch`
  kept) as a rollback path, to be deleted once `pipeline.yml` has proven
  itself over a few weeks.
