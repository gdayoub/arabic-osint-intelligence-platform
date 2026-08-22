# read this first

You're picking this up from a Claude Code session dated 2026-08-22. This file
covers what that session actually did, in commit order, so you don't have to
reverse-engineer it from diffs. Read `docs/AGENT_BRIEF.md` for the spec and
`AGENTS.md` for George's own standing notes first — this is the delta on top
of those, and it flags the two places `AGENTS.md` is now stale.

**Read `AGENTS.md`'s "how i want to work" section and follow it.** Explain
before you build, stop at checkpoints, write an ADR for real decisions, no
dependency without justifying it. That did not change.

## AGENTS.md is stale in two places

It still says "current live numbers: 451 documents, 6084 mentions, 61
entities" and "M5 bilingual" as not-yet-started, with 251 tests passing.
Those numbers predate this session. As of this handoff:

- **21 ingestion sources** are wired into `src/pipeline/ingest_core.py`'s
  `build_scrapers()` (was 3 pre-session: Al Jazeera, BBC Arabic, CNN Arabic,
  plus a permanently-broken Al Arabiya that has since been removed).
- **471 unit tests passing** (was 251; entity-resolution and translation work
  from before this session already grew it past 251, this session added 12
  more for the M5 work below).
- **M5 (bilingual layer) is now in progress**, not unstarted — see below.

Don't trust the entity/document/mention counts in `AGENTS.md` at all; those
move every pipeline run and nobody updated the prose. Check the live
dashboard (`https://arabic-osint-dashboard.georgedayoub500.workers.dev`) or
query Neon directly for current numbers.

## what this session did, in order

### 1. Fixed a dead entity-review pipeline

`scripts/apply_review_issue.py` was missing a `sys.path` insertion of the
repo root, so every CI run trying to apply a human review decision
(ADR 0014's GitHub-Issue write bridge) crashed with `ModuleNotFoundError`.
This meant the entire review-queue-to-database pipeline had silently never
worked. Fixed, then verified end-to-end by submitting 12 real review
decisions and confirming the dashboard's `review_queue.items` count dropped
to 0.

### 2. Added `--review-margin`/`--review-limit` as pipeline knobs

`main.py`, `src/ops/runtime.py`, `.github/workflows/pipeline.yml` — so a
bulk-labeling session can widen the review margin via `workflow_dispatch`
without a code change. (Empirically this didn't surface more pairs with the
current 63-entity corpus — the real bottleneck is corpus size, not margin.)

### 3. Grew ingestion from 3 working sources to 21

This was the bulk of the session, done in several rounds, each researched
with parallel fork/sub-agents (curl-based verification: robots.txt, real
listing links, real server-rendered `<p>` body text vs. meta-tag-only
"client-rendered" traps, parseable dates, bot-blocking checks) and then
**live-verified by actually running `scraper.scrape()` against the real site
in a throwaway venv** before shipping — every single one of the 18 new
scrapers hit a real bug during that live-verify step that the research pass
missed. The recurring failure modes, so you don't rediscover them:

- **`canonicalize_url()` strips trailing slashes** before `is_valid_article_url()`
  ever sees the URL, but `extract_article_links()` sees the raw href *with*
  the slash. An article-path regex anchored with `$` and no `/?` before it
  will silently match zero links. Bit almost every new scraper (Libya
  Al-Ahrar before this session, Al Mada in it). Always write article regexes
  as `...\d+/?$`, not `...\d+$`.
- **A CSS class name existing on the page doesn't mean it's unique to the
  real article body.** Al Chourouk's `field--name-body field--type-text-with-summary`
  also appears on a sidebar social-links widget and a newsletter box — the
  real body needed the more specific `[property='schema:text']` attribute.
  Akhbar Al Khaleej's date lives in `<meta itemprop="datePublished">`, not
  the `og:`/`article:published_time` convention the shared
  `extract_meta_datetime()` helper checks. Always fetch one real article and
  grep the actual HTML around the `<p>` tags / date value before trusting a
  selector guessed from a `curl | grep` sample.
- **`dateutil.parser.parse()` silently misparses ambiguous day/month order.**
  Ammon News's JSON-LD date is `DD-MM-YYYY`. An unambiguous sample
  (`16-08-2026`, day > 12) parsed fine and looked like proof the format
  worked; a second article with an ambiguous date (`08-12-2026`) got
  silently read as December instead of August. Wrote a custom regex parser
  instead of trusting dateutil's default American-order guess whenever the
  source's date format isn't unambiguous.
- **Some sites' date is only available as Arabic prose or plain text**, not
  any tag convention: Al Masdar Online's `<time datetime="24 فبراير 2026">`
  (Arabic month name, needed a lookup table), Wafa's plain-text
  `"تاريخ النشر: 22/08/2026 08:26 ص"` (needed a regex + ص/م AM/PM mapping).
  Al Chourouk's date meta tag is a raw Unix-epoch integer string, not
  ISO-8601. `dateutil` can't parse any of these; each got a small dedicated
  parser in its own scraper file (not a shared utility — only one scraper
  each needed it).
- **A listing page can have zero real `<a href>` to articles at all.**
  Donia Al Watan's listing only exposes the real article URL as a query
  string on Facebook/Twitter share-widget links (whose own `href` points at
  facebook.com, which `_is_same_domain()` correctly rejects).
  `extract_article_links()` regex-scans the raw HTML text for the URL
  pattern directly instead of walking `<a>` tags — same trick as sitemap/RSS
  scraping (An-Nahar), just applied to ordinary HTML instead of XML.
- **A real Cloudflare/Radware bot-management block looks like this**: `server:
  cloudflare` header, `cf-ray` header, a challenge page even on
  `/robots.txt`, an "Access Denied" page in the site's own language. That is
  not a fixable selector bug and was never pursued — see "sources rejected"
  below. Don't spend time trying to bypass one; it's out of scope for this
  project (and generally not something to attempt).

**Sources added (21 total):** Al Jazeera, BBC Arabic, CNN Arabic (pre-existing),
An-Nahar (Lebanon, via news sitemap not category pages), Youm7 (Egypt), Libya
Al-Ahrar, Al Khaleej (UAE), Al-Masry Al-Youm (Egypt), SANA (Syria — state
media, explicitly flagged as such in its docstring and `.env.example`,
`MIN_DELAY_SECONDS=5.0` to honor its robots.txt Crawl-delay), Asharq
Al-Awsat, Al Mada (Iraq), Al Chourouk (Tunisia), Ammon News (Jordan),
Hespress (Morocco), Akhbarona (Morocco), Al Rai (Kuwait), Sudan Tribune
(`.net`, not `.com` — `.com` is bot-protected), Al Masdar Online (Yemen),
Akhbar Al Khaleej (Bahrain), Wafa (Palestine), Donia Al Watan / alwatanvoice.com
(Palestine).

**Sources researched and rejected** (don't re-research these — the reasons
are durable, not transient): Sky News Arabia, Rudaw, Echorouk, Al Watan
(Saudi), Al Riyadh, ONA (Oman — no machine-readable date anywhere, only a
prose dateline), Okaz, QNA, Petra, Shafaq News, Al Quds (Palestine),
Naharnet, Al-Eqtisadiah, Al Sabaah (Iraq — Cloudflare), Mosaique FM (Tunisia
— JS-hydrated listing), Al Ghad (Jordan — Cloudflare, blocks even
robots.txt), Al Qabas (Kuwait — JS-hydrated listing).

### 4. Removed Al Arabiya

It was wired in before this session and permanently returning HTTP 403
("تم رفض الوصول" / access denied) with Cloudflare headers on every request —
zero-yield dead weight in every pipeline run, not a fixable bug. Removed from
both `src/pipeline/ingest_core.py` (current path) and the legacy
`src/pipeline/ingest_pipeline.py` (`raw_articles` path, still imported by
`main.py`/`run_pipeline.py`/`api/main.py` even though it's superseded —
deleting the scraper file broke its import too, fixed in the same commit).
One test (`test_ops_runtime.py`) hardcoded its presence in
`core_component_versions()`'s expected set; updated.

### 5. Started M5 (bilingual layer)

Two pieces landed, two remain (see "what's not done" below).

- **`src/lang/arabic.py`'s `romanize()`** was a single letter-by-letter guess
  per name (the file's own comment called it "deliberately basic... M5 is
  where romanization gets done properly"). Replaced with real multi-candidate
  generation: a variant table (`_ROMAN_VARIANTS`) for letters people
  genuinely spell more than one way (ث ج ذ ض ظ ع غ ق), crossed per-token via
  `itertools.product` and then across tokens, so a full name produces
  combined candidates, not just the first word varying. Capped at 12
  candidates per token and 12 per full name — a name with several ambiguous
  letters multiplies fast and this is a search-key budget, not a linguistic
  claim. The existing known-name lookup table (`_KNOWN_ROMANIZATIONS`, ~10
  common names with real labelled spellings) still takes priority per token
  over the generated guess.
- **New `src/search/entity_search.py`** — `search_entities(session, query,
  limit=20) -> list[Entity]`. Detects script via `src/lang.REGISTRY.detect()`.
  An Arabic query matches directly against normalized canonical names. An
  English query goes through two independent bridges, unioned: the
  translation cache (`TranslationORM` — an already-translated name matches
  directly) and the romanized candidates above (for names not yet
  translated, or where the query is a transliteration rather than a
  translation). Both matter independently: a fresh entity has no cached
  translation yet, and a name outside the known-name lookup only romanizes to
  a rough letter-level guess a real person might not type.

## what's not done (M5 remainder + everything after)

Per `docs/AGENT_BRIEF.md`'s M5 section, still open:

- **`search_entities()` isn't wired to anything yet.** It's a tested
  function, not an API endpoint or dashboard feature. `src/api/main.py` is
  currently frozen/dead code per ADR 0010 (fastapi, not deployed) — decide
  with George whether search belongs there, in a new endpoint, or stays a
  library call for now.
- **No dedicated evidence-sentence translation flow.** Title translation
  exists (`src/pipeline/translate_core.py`); entity names and evidence
  sentences specifically are still the brief's stated gap.
- **The golden romanization test set is thin** (10 known names). The brief's
  M5 checkpoint is "search in English, get the right Arabic entity, see both
  spellings and a translated evidence sentence with the Arabic beside it" —
  the UI side of that (showing original + translation side by side, never
  translation alone) doesn't exist since there's no consuming UI yet.
- **M6 (links and the graph) through M11 are untouched** — see
  `docs/AGENT_BRIEF.md` for what each covers. M6 is next in brief order.

Everything from `AGENTS.md`'s own "known problems i have not fixed" section
(the learned pair scorer matching 0 pairs in production, the training set
being drawn wrong, the gazetteer being ~100 names) is **still true** — this
session didn't touch entity resolution internals at all, only ingestion
breadth and the bilingual layer.

## the Vercel/portfolio deploy chain (non-obvious, don't skip)

This repo bakes `data.json` and deploys it to a Cloudflare Worker
(`arabic-osint-dashboard.georgedayoub500.workers.dev`). A **separate** repo,
`gdayoub/george-portfolio` (Vercel-hosted, checked out locally at
`~/Documents/Portfolio` on this machine), consumes that `data.json`
cross-origin at `/osint-dashboard.html` and via a homepage widget. The two
are linked by a versioned contract in `contracts/dashboard/` — run
`python scripts/generate_dashboard_contract.py --check` before declaring any
dashboard-shape change done. As of this handoff the contract is current and
the portfolio's pinned consumer commit is already merged into its
`origin/main`, so the live Vercel site is fine. The *local* portfolio
checkout is a different story: it's on a diverged branch missing the OSINT
integration commits, with uncommitted `.next/` build noise — don't commit
through it without syncing first, and that sync is portfolio-repo work, not
part of this repo.

## running it

Same as `AGENTS.md` describes, current count:

```bash
python -m pytest tests/unit -q                    # 471 passing, 1 skipped, 2 xfailed
python scripts/generate_dashboard_contract.py --check
python main.py --help
```

Every scraper this session added was verified with a throwaway venv, not the
system Python — `requirements.txt` needs `pydantic==2.12.5` exactly for the
contract generator, and torch/transformers only belong in
`requirements-ml.txt` for local scorer work, never the scheduled pipeline.

## working style note for whoever's next

Commit and push finished, verified work as you go — don't batch it up and
leave it sitting in the working tree. George works across multiple sessions
on this repo and needs to be able to hand it to another agent (like this
file exists to do) without anything uncommitted getting lost.
