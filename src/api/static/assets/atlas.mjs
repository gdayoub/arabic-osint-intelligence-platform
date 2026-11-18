import {
  normalize,
  safeURL,
  dateMillis,
  filterArticles,
  summarize,
  countriesFor,
  colorFor,
  dailyCounts,
  parseState,
  geographyAliases,
  regionMarkers,
} from "./atlas-model.mjs";

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (value) => Number(value).toLocaleString("en-US");
const pct = (value) => (value === null ? "—" : Math.round(value * 100) + "%");
const date = (value) =>
  value && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      })
    : "Date unavailable";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let state = parseState(location.search),
  snapshot = null,
  currentSnapshot = null,
  articles = [],
  entities = [],
  filtered = [],
  geometries = [],
  globe = null,
  globeStarted = false,
  listMode = false,
  pageSize = 20,
  compare = [],
  demo = false,
  toastTimer,
  countrySummaries = new Map();
let board = {};
try {
  const saved = JSON.parse(
    localStorage.getItem("osint-atlas-board-v1") || "{}",
  );
  if (saved && typeof saved === "object" && !Array.isArray(saved))
    board = Object.fromEntries(
      Object.entries(saved).filter(
        ([, v]) =>
          v &&
          typeof v === "object" &&
          v.article &&
          typeof v.article.title === "string",
      ),
    );
} catch {
  /* A corrupt or unavailable browser store must not prevent reading. */
}
const articleKey = (a) => a.url || `${snapshot?.generated_at}:${a.id}`;
const isSaved = (a) => Boolean(board[articleKey(a)]);
const empty = (title, body) =>
  `<div class="empty"><h2>${esc(title)}</h2><p>${esc(body)}</p></div>`;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 3500);
}
function persistBoard() {
  try {
    localStorage.setItem("osint-atlas-board-v1", JSON.stringify(board));
    return true;
  } catch {
    toast("Browser storage is unavailable. Export your notes before leaving.");
    return false;
  }
}
async function fetchJSON(path) {
  const r = await fetch(path, { signal: AbortSignal.timeout(25000) });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
function updateURL() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state))
    if (value && key !== "entity" && (key !== "view" || value !== "explore"))
      params.set(key, value);
  if (demo) params.set("demo", "1");
  if (compare.length) params.set("compare", compare.join(","));
  history.replaceState(
    null,
    "",
    location.pathname + (params.size ? "?" + params : ""),
  );
}
function syncInputs() {
  for (const [id, key] of [
    ["query", "q"],
    ["source", "source"],
    ["topic", "topic"],
    ["level", "level"],
    ["date-from", "from"],
    ["date-to", "to"],
  ])
    $(id).value = state[key] || "";
}
function populateFilters() {
  for (const [id, field, label] of [
    ["source", "source", "All sources"],
    ["topic", "topic", "All topics"],
  ])
    $(id).innerHTML =
      `<option value="">${label}</option>` +
      [...new Set(articles.map((a) => a[field]).filter(Boolean))]
        .sort()
        .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
        .join("");
  syncInputs();
}
function apply() {
  if (!snapshot) return;
  pageSize = 20;
  syncInputs();
  updateURL();
  render();
}
function selectCountry(name) {
  state.country = name;
  state.view = "explore";
  apply();
  focusCountry(name);
}
function findArticle(id) {
  if (id.startsWith("saved:")) return board[id.slice(6)]?.article;
  return (
    articles.find((a) => a.id === id) ||
    Object.values(board).find((item) => item.article.id === id)?.article
  );
}
function saveArticle(id) {
  const a = findArticle(id);
  if (!a) return;
  const key = id.startsWith("saved:") ? id.slice(6) : articleKey(a);
  if (board[key]) {
    delete board[key];
    toast("Removed from your research board.");
  } else {
    board[key] = {
      article: a,
      note: "",
      snapshot: snapshot.generated_at,
      classification: snapshot.classification,
      saved_at: new Date().toISOString(),
    };
    toast("Saved to your research board.");
  }
  persistBoard();
  render();
}

async function loadSnapshot() {
  const index = await fetchJSON("data.json");
  try {
    const atlas = await fetchJSON("countries/_atlas.json");
    if (
      atlas.atlas_version !== 1 ||
      !atlas.complete ||
      !Array.isArray(atlas.articles) ||
      !Array.isArray(atlas.entities)
    )
      throw new Error("Unsupported archive");
    if (
      atlas.generated_at !== index.generated_at ||
      atlas.articles.length !== index.stats.total_processed
    )
      throw new Error("Archive and summary belong to different snapshots");
    return { ...atlas, index, mode: "complete" };
  } catch (error) {
    // Legacy sites have only limited country pages. Never call this full archive.
    const pages = await Promise.allSettled(
      (index.countries || []).map((c) =>
        fetchJSON(`countries/${encodeURIComponent(c.slug)}.json`),
      ),
    );
    const byURL = new Map();
    let failed = 0;
    for (const result of pages) {
      if (
        result.status !== "fulfilled" ||
        result.value.generated_at !== index.generated_at
      ) {
        failed++;
        continue;
      }
      for (const article of result.value.articles || []) {
        const key = article.url || article.title;
        byURL.set(key, {
          ...article,
          id: `sample-${byURL.size}`,
          country: result.value.country,
          collected_at: article.processed_at,
          entity_ids: [],
        });
      }
    }
    for (const article of index.recent || []) {
      const key = article.url || article.title;
      if (!byURL.has(key))
        byURL.set(key, {
          ...article,
          id: `sample-${byURL.size}`,
          collected_at: article.processed_at,
          entity_ids: [],
        });
    }
    if (!byURL.size) throw new Error("No readable reporting snapshot");
    const ranked = [];
    for (const [type, rows] of Object.entries(index.entities?.top || {}))
      for (const row of rows)
        ranked.push({
          ...row,
          id: `sample-entity-${ranked.length}`,
          name_en: null,
          object_type: type,
          romanizations: [],
        });
    return {
      generated_at: index.generated_at,
      complete: false,
      articles: [...byURL.values()],
      entities: ranked,
      index,
      mode: "sample",
      failed,
      classification: {
        name: "rule_based_document_classifier",
        version: "2.0.1",
      },
    };
  }
}
function activate(data) {
  snapshot = data;
  articles = data.articles;
  entities = data.entities;
  populateFilters();
  apply();
  startGlobe();
}
function renderStatus() {
  const age = Date.now() - Date.parse(snapshot.generated_at);
  $("snapshot-time").textContent =
    `${demo ? "EXAMPLE" : "SNAPSHOT"} · ${date(snapshot.generated_at)}`;
  const prefix = demo ? "Frozen example · " : "";
  const status = snapshot.complete
    ? `${prefix}${number(articles.length)} searchable articles · ${number(new Set(articles.map((a) => a.source)).size)} publishers · updated ${date(snapshot.generated_at)}. Refreshes after scheduled pipeline runs.`
    : `${prefix}Public sample: ${number(articles.length)} articles from ${number(snapshot.index?.stats?.total_processed || articles.length)} processed. The complete archive appears after the updated pipeline runs.${snapshot.failed ? " Some country files could not be loaded." : ""}`;
  $("load-status").textContent =
    status +
    (!demo && age > 12 * 3600000
      ? " This snapshot is more than 12 hours old."
      : "");
  $("load-status").classList.toggle(
    "warning",
    !snapshot.complete || (!demo && age > 12 * 3600000),
  );
  $("demo-toggle").textContent = demo
    ? "Return to current snapshot"
    : "Open example snapshot";
}
function render() {
  renderStatus();
  filtered = filterArticles(articles, state, entities);
  const sort = $("sort").value;
  filtered.sort(
    (a, b) =>
      (sort === "high"
        ? ({ high: 3, medium: 2, low: 1 }[b.escalation] || 0) -
          ({ high: 3, medium: 2, low: 1 }[a.escalation] || 0)
        : 0) || (dateMillis(b) || 0) - (dateMillis(a) || 0),
  );
  const titles = {
    explore: ["THE WORLD, THROUGH ARABIC REPORTING", "Explore the signals."],
    search: ["SEARCH THE REPORTING", "Follow your curiosity."],
    entities: ["PEOPLE, PLACES & ORGANIZATIONS", "Names behind the news."],
    saved: ["YOUR RESEARCH, IN ONE PLACE", "Build a clearer picture."],
  };
  $("view-eyebrow").textContent = titles[state.view][0];
  $("view-title").textContent = titles[state.view][1];
  for (const b of document.querySelectorAll("[data-view]")) {
    const active = b.dataset.view === state.view;
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  for (const v of ["explore", "entities", "saved"])
    $(v + "-view").hidden = state.view !== v;
  $("reporting-section").hidden = ["entities", "saved"].includes(state.view);
  $("filters").hidden = state.view === "saved";
  document.querySelector(".timebar").hidden = state.view === "saved";
  $("active-filters").hidden = state.view === "saved";
  $("active-filters").innerHTML = ["country", "entity"]
    .filter((k) => state[k])
    .map(
      (k) =>
        `<button class="active-tag" data-clear="${k}">${esc(k === "entity" ? entities.find((e) => e.id === state[k])?.name || "Entity" : state[k])} ×</button>`,
    )
    .join("");
  $("saved-count").textContent = Object.keys(board).length;
  const available = filterArticles(articles, state, entities, true);
  const countries = countriesFor(available);
  countrySummaries = new Map(countries.map((c) => [c.name, c]));
  $("country-list").innerHTML = countries.length
    ? countries.map(countryRow).join("")
    : empty("No country reporting", "Reset a filter or widen the date range.");
  renderBriefing(countries);
  renderTimeline();
  renderComparison(available);
  renderArticles();
  renderEntities();
  renderSaved();
  updateGlobe(countries);
  if (globe) {
    if (state.view === "explore" && !listMode && !document.hidden)
      globe.resumeAnimation();
    else globe.pauseAnimation();
  }
}
function countryRow(c) {
  return `<button class="country-row" data-country="${esc(c.name)}"><span><i class="signal-dot" style="background:${colorFor(c)}"></i>${esc(c.name)}</span><span>${number(c.total)} ↗</span></button>`;
}
function distribution(s) {
  return `<div class="distribution" aria-label="${s.levels.high} high, ${s.levels.medium} medium, ${s.levels.low} low"><span style="flex:${s.levels.high};background:var(--red)"></span><span style="flex:${s.levels.medium};background:var(--amber)"></span><span style="flex:${s.levels.low};background:var(--green)"></span></div><div class="dist-legend"><span>${s.levels.high} HIGH</span><span>${s.levels.medium} MED</span><span>${s.levels.low} LOW</span></div>`;
}
function renderBriefing(countries) {
  const s = summarize(filtered);
  const selected = state.country;
  const leaders = [...countries]
    .filter((c) => c.total >= 5)
    .sort((a, b) => b.highShare - a.highShare || b.total - a.total)
    .slice(0, 4);
  $("briefing").innerHTML =
    `<span class="eyebrow">${selected ? "COUNTRY BRIEFING" : "IN THIS VIEW"}</span><h2>${esc(selected || "Regional overview")}</h2><p class="intro">${selected ? "Explore the sources and signals behind this coverage." : "Choose a country to explore its reporting."}</p><div class="briefing-metrics"><div class="metric"><strong>${number(s.total)}</strong><small>articles in view</small></div><div class="metric"><strong>${pct(s.highShare)}</strong><small>high-escalation share</small></div><div class="metric"><strong>${s.sources}</strong><small>publishers represented</small></div></div>${distribution(s)}<p class="briefing-note">${s.total < 5 ? "Thin sample. Read the articles before drawing a conclusion." : "Colors describe reporting, not a prediction or a safety rating."}</p>${
      selected
        ? `<span class="eyebrow">LEADING TOPICS</span><div class="ranked-list">${s.topics
            .slice(0, 3)
            .map(
              ([name, count]) =>
                `<button class="country-row" data-topic="${esc(name)}"><span>${esc(name)}</span><span>${count}</span></button>`,
            )
            .join(
              "",
            )}</div><button id="compare-add" class="button">${compare.includes(selected) ? "Added to comparison ✓" : "+ Compare this country"}</button>`
        : `<span class="eyebrow">HIGHEST ESCALATION SHARE · 5+ ARTICLES</span><div class="ranked-list">${leaders.map(countryRow).join("") || '<p class="subtle">No countries with five articles in this view.</p>'}</div>`
    }`;
  $("globe-title").textContent = selected || "A different perspective.";
}
function renderTimeline() {
  const rows = filterArticles(
    articles,
    { ...state, from: "", to: "" },
    entities,
  );
  const counts = dailyCounts(rows);
  if (!counts.length) {
    $("timeline").innerHTML =
      '<p class="subtle">No publication dates available in this view.</p>';
    return;
  }
  const last = Date.parse(counts.at(-1)[0] + "T00:00:00Z"),
    first = Math.max(
      Date.parse(counts[0][0] + "T00:00:00Z"),
      last - 29 * 86400000,
    ),
    lookup = new Map(counts),
    days = [];
  for (let t = first; t <= last; t += 86400000) {
    const key = new Date(t).toISOString().slice(0, 10);
    days.push([key, lookup.get(key) || 0]);
  }
  const max = Math.max(1, ...days.map((d) => d[1]));
  $("timeline").innerHTML = days
    .map(
      ([day, count]) =>
        `<button class="day-bar" data-day="${day}" data-label="${esc(date(day).replace(", " + day.slice(0, 4), ""))}" title="${day} · ${count} articles" aria-label="${day}: ${count} articles. Filter to this day." style="height:${Math.max(3, (count / max) * 100)}%;${state.from === day && state.to === day ? "background:var(--accent)" : ""}"></button>`,
    )
    .join("");
  const missing = rows.filter((a) => !Number.isFinite(dateMillis(a))).length;
  $("timeline-caption").textContent =
    `Latest ${days.length} publication days${missing ? " · " + missing + " undated excluded" : ""}`;
}
function renderComparison(available) {
  $("comparison").hidden = !compare.length;
  if (!compare.length) return;
  $("comparison").innerHTML =
    `<div class="section-heading"><h2>Country comparison</h2><button id="compare-clear" class="text-button">Clear comparison</button></div><p class="subtle">Same search, source, topic, escalation, and date filters. Select another country and add it to compare.</p><div class="compare-grid">${compare
      .map((name) => {
        const s = summarize(available.filter((a) => a.country === name));
        return `<div class="compare-country"><h3><button data-country="${esc(name)}">${esc(name)}</button><button data-uncompare="${esc(name)}" aria-label="Remove ${esc(name)} from comparison">×</button></h3><p>${number(s.total)} articles · ${s.sources} publishers</p><p><strong style="color:${colorFor(s)}">${pct(s.highShare)}</strong> high-escalation share</p>${distribution(s)}</div>`;
      })
      .join("")}</div>`;
}
function articleHTML(a, index, withNote = false, key = "") {
  const level = ["high", "medium", "low"].includes(a.escalation)
    ? a.escalation
    : "unknown";
  const url = safeURL(a.url);
  const displayId = withNote ? "saved:" + key : a.id;
  return `<article class="article-card"><span class="article-number">${String(index + 1).padStart(2, "0")}</span><div><div class="article-meta"><button class="badge ${level}" data-detail="${esc(displayId)}">${level}</button><span>${esc(a.source)}</span><span>·</span><span>${date(a.published_date)}</span>${a.country ? `<button data-country="${esc(a.country)}">${esc(a.country)}</button>` : ""}</div><button class="article-title" data-detail="${esc(displayId)}" ${!a.title_en ? 'lang="ar" dir="rtl"' : ""}>${esc(a.title_en || a.title)}</button>${a.title_en ? `<div class="article-arabic" lang="ar">${esc(a.title)}</div>` : ""}<div class="article-bottom"><span>${a.title_en ? "Machine-translated headline" : "English translation unavailable"}</span>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Original source ↗</a>` : ""}<button data-detail="${esc(displayId)}">Inspect evidence</button></div>${withNote ? `<label class="sr-only" for="note-${index}">Your notes for ${esc(a.title_en || a.title)}</label><textarea id="note-${index}" class="saved-note" data-note="${esc(key)}" placeholder="Your notes, questions, or observations…">${esc(board[key]?.note || "")}</textarea>` : ""}</div><button class="save-button" data-save="${esc(displayId)}" aria-pressed="${isSaved(a)}" aria-label="${isSaved(a) ? "Remove saved article" : "Save article"}">${isSaved(a) ? "▣" : "⊞"}</button></article>`;
}
function renderArticles() {
  const limit = state.view === "explore" ? Math.min(pageSize, 8) : pageSize;
  $("article-results").innerHTML = filtered.length
    ? filtered
        .slice(0, limit)
        .map((a, i) => articleHTML(a, i))
        .join("")
    : empty(
        "No matching reporting",
        "Try another name, clear a filter, or widen the publication dates.",
      );
  $("result-count").textContent =
    `${number(filtered.length)} ${snapshot.complete ? "articles" : "sample articles"}`;
  $("feed-title").textContent =
    state.view === "search"
      ? "Search results"
      : state.country
        ? `Reporting on ${state.country}`
        : "Latest reporting";
  $("load-more").hidden = filtered.length <= limit;
  $("load-more").textContent =
    state.view === "explore"
      ? "Explore all matching articles →"
      : "Show more articles";
}
function renderEntities() {
  const query = normalize(state.q).trim();
  const articleCounts = new Map();
  for (const a of filtered)
    for (const id of a.entity_ids || [])
      articleCounts.set(id, (articleCounts.get(id) || 0) + 1);
  const list = entities
    .filter(
      (e) =>
        !query ||
        normalize(
          [
            e.name,
            e.name_en,
            ...(e.surface_forms || []),
            ...(e.romanizations || []),
          ].join(" "),
        ).includes(query),
    )
    .filter((e) => !snapshot.complete || articleCounts.has(e.id))
    .sort(
      (a, b) =>
        (articleCounts.get(b.id) || b.count || 0) -
        (articleCounts.get(a.id) || a.count || 0),
    );
  $("entity-results").innerHTML = list.length
    ? list
        .map(
          (e) =>
            `<button class="entity-card" data-entity="${esc(e.id)}"><span class="eyebrow">${esc(e.object_type)}</span><h3>${esc(e.name_en || "")}</h3><div class="entity-ar" lang="ar">${esc(e.name)}</div><p>${number(articleCounts.get(e.id) || e.count || 0)} ${snapshot.complete ? "matching articles" : "mentions in snapshot"} · ${(e.surface_forms || []).length} attested forms</p></button>`,
        )
        .join("")
    : empty(
        "No matching entities",
        "Try an Arabic name or a different spelling. Generated romanizations are search aids, not verified aliases.",
      );
}
function renderSaved() {
  const entries = Object.entries(board);
  $("saved-results").innerHTML = entries.length
    ? entries
        .map(([key, item], i) => articleHTML(item.article, i, true, key))
        .join("")
    : empty(
        "Start a research board",
        "Save an article using the + button, add your own notes, then print a sourced briefing.",
      );
}
function openDialog(label, html) {
  $("dialog-label").textContent = label;
  $("detail-content").innerHTML = html;
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
}
function showArticle(id) {
  const a = findArticle(id);
  if (!a) return;
  const saved = id.startsWith("saved:") ? board[id.slice(6)] : null;
  const sameSnapshot = !saved || saved.snapshot === snapshot.generated_at;
  const classifier = saved ? saved.classification : snapshot.classification;
  const linked = sameSnapshot ? entities.filter((e) => (a.entity_ids || []).includes(e.id)) : [];
  const url = safeURL(a.url);
  openDialog(
    "ARTICLE EVIDENCE",
    `<h2>${esc(a.title_en || a.title)}</h2>${a.title_en ? '<p class="subtle">Machine-translated headline · original below</p>' : ""}<div class="original" lang="ar">${esc(a.title)}</div><div class="detail-grid"><div><span>Publisher</span><strong>${esc(a.source)}</strong></div><div><span>Country classification</span><strong>${esc(a.country || "Unassigned")}</strong></div><div><span>Published · UTC</span><strong>${esc(a.published_date || "Unavailable")}</strong></div><div><span>Collected · UTC</span><strong>${esc(a.collected_at || "Unavailable")}</strong></div><div><span>Escalation</span><strong>${esc(a.escalation || "Unknown")}</strong></div><div><span>Classifier</span><strong>${esc(classifier?.name || "Unavailable")} ${esc(classifier?.version || "")}</strong></div></div><h3>How to read this signal</h3><p>The rule-based classifier checks the original article for escalation-related Arabic keywords. This label describes reporting language. It does not establish that an event occurred or predict a country's safety. Exact scoring matches and body excerpts are not in this public export.</p>${linked.length ? `<h3>Entities mentioned in the article</h3>${linked.map((e) => `<button class="alias" data-entity="${esc(e.id)}" lang="ar">${esc(e.name)}</button>`).join("")}` : '<p class="briefing-note">Entity-to-article evidence is not available for this record.</p>'}<div class="detail-links">${url ? `<a class="button accent" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Read original source ↗</a>` : ""}<button class="button" data-save="${esc(id)}">${isSaved(a) ? "Remove from board" : "Save to research board"}</button></div>`,
  );
}
function showEntity(id) {
  const e = entities.find((x) => x.id === id);
  if (!e) return;
  const rows = articles.filter((a) => (a.entity_ids || []).includes(e.id));
  const s = summarize(rows);
  openDialog(
    "ENTITY DOSSIER",
    `<span class="eyebrow">${esc(e.object_type)}</span><h2>${esc(e.name_en || e.name)}</h2>${e.name_en ? `<div class="original" lang="ar">${esc(e.name)}</div><p>Machine-translated entity name.</p>` : ""}<h3>Attested spellings</h3>${(e.surface_forms?.length ? e.surface_forms : [e.name]).map((name) => `<span class="alias" lang="ar">${esc(name)}</span>`).join("")}<h3>Reporting coverage</h3><p>${snapshot.complete ? `${s.total} distinct articles across ${s.sources} publishers in this snapshot.` : "This public sample includes global entity rankings but does not include their article links."}</p>${e.romanizations?.length ? `<details><summary>Generated search spellings</summary><p>${e.romanizations.map(esc).join(" · ")}</p><p>These are generated search keys, not verified aliases.</p></details>` : ""}<p class="briefing-note">Resolver IDs are snapshot-specific. Share a name search to find this entity in future snapshots.</p>${
      rows.length
        ? `<div class="detail-links"><button class="button accent" data-entity-filter="${esc(e.id)}">Explore ${s.total} articles →</button></div><h3>Recent supporting articles</h3>${rows
            .sort((a, b) => (dateMillis(b) || 0) - (dateMillis(a) || 0))
            .slice(0, 4)
            .map(
              (a) =>
                `<button class="country-row" data-detail="${esc(a.id)}">${esc(a.title_en || a.title)}</button>`,
            )
            .join("")}`
        : '<div class="detail-links"><button class="button" data-name-search="' +
          esc(e.name) +
          '">Search this name →</button></div>'
    }`,
  );
}
function showMethod() {
  openDialog(
    "METHOD & COVERAGE",
    `<h2>Understand the view.</h2><h3>What the colors mean</h3><p>Country color represents the share of classified articles labeled high escalation in your current filters: high ÷ (high + medium + low). The scale is fixed across countries and uses a square-root color interpolation to make smaller differences visible. Gray means no classified reporting in the view. A low label can mean no keyword matches.</p><h3>What the map means</h3><p>Articles are assigned one country or area by a keyword classifier; this is reporting attribution, not verified incident geolocation. Geography uses Natural Earth 1:110m administrative boundaries. Gaza is a separate reporting category represented by an approximate area marker; Bahrain also uses a marker at this scale. Markers are navigation aids, not event coordinates. The dataset's boundary convention is used without modifying disputed boundaries.</p><h3>Dates and completeness</h3><p>Filters use publication dates in UTC. Undated articles remain in the all-dates archive and are excluded from date-filtered results and timelines. A publication replay shows today's collected corpus, not what the system knew at a past date. The interface explicitly labels a limited sample when the complete archive is absent.</p><h3>Evidence & language</h3><p>English headlines are machine translations; Arabic originals stay alongside them. Entity spellings are attested forms from resolution. Generated romanizations are separate search aids. Public exports contain article metadata and entity memberships, never full article bodies.</p><h3>Research board</h3><p>Saved articles and notes stay in this browser. A shared view link shares filters, not your private notes. Export your board or print a briefing to retain a copy. Saved articles may outlive the current public snapshot.</p><div class="detail-links"><a class="button" href="engine.html#review-container">Open entity review ↗</a><a class="button" href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noopener noreferrer">Boundary attribution ↗</a></div>`,
  );
}

async function startGlobe() {
  if (globeStarted) return;
  globeStarted = true;
  try {
    const geo = await fetchJSON("assets/countries.geojson");
    geometries = geo.features.filter((f) => f.properties.name !== "Antarctica");
    for (const f of geometries)
      f.atlasName = geographyAliases[f.properties.name] || f.properties.name;
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "assets/globe.gl.min.js";
      script.onload = resolve;
      script.onerror = reject;
      document.head.append(script);
    });
    const container = $("globe");
    const stage = $("globe-stage");
    globe = new window.Globe(container, {
      animateIn: !reduced,
      rendererConfig: { antialias: true, alpha: true },
    })
      .width(stage.clientWidth || 650)
      .height(stage.clientHeight || 455)
      .backgroundColor("#0e1721")
      .showAtmosphere(true)
      .atmosphereColor("#537c82")
      .atmosphereAltitude(0.13)
      .polygonsData(geometries)
      .polygonAltitude((f) => (f.atlasName === state.country ? 0.024 : 0.006))
      .polygonCapColor((f) => colorFor(countrySummaries.get(f.atlasName)))
      .polygonSideColor(() => "#273e47")
      .polygonStrokeColor(() => "#516474")
      .polygonsTransitionDuration(reduced ? 0 : 250)
      .polygonLabel((f) => {
        const s = countrySummaries.get(f.atlasName);
        return `<div style="background:#13202b;padding:12px;border:1px solid #425768;border-radius:6px;font:14px system-ui"><b>${esc(f.atlasName)}</b><br>${s ? `${s.total} articles · ${pct(s.highShare)} high escalation` : "No reporting in this view"}</div>`;
      })
      .onPolygonClick((f) => {
        if (articles.some((a) => a.country === f.atlasName))
          selectCountry(f.atlasName);
        else toast("No reporting assigned to this country in the snapshot.");
      })
      .labelsData(regionMarkers)
      .labelLat("lat")
      .labelLng("lng")
      .labelText("name")
      .labelSize(0.7)
      .labelDotRadius(0.25)
      .labelColor(() => "#d4eee8")
      .labelResolution(2)
      .labelAltitude(0.035)
      .onLabelClick((marker) => selectCountry(marker.name));
    globe.globeMaterial().color.set("#0e1721");
    globe.controls().enablePan = false;
    globe.controls().minDistance = 150;
    globe.controls().maxDistance = 650;
    globe.pointOfView({ lat: 25, lng: 36, altitude: 1.75 }, 0);
    new ResizeObserver(() => {
      if (stage.clientWidth && stage.clientHeight)
        globe.width(stage.clientWidth).height(stage.clientHeight);
    }).observe(stage);
    $("globe-loading").hidden = true;
    render();
  } catch (error) {
    console.warn("Globe unavailable; country list retained.", error);
    $("globe-loading").textContent =
      "3D is unavailable on this device. Explore the same reporting in the country list.";
    setMapMode(true);
  }
}
function updateGlobe(countries) {
  if (!globe) return;
  const byName = new Map(countries.map((c) => [c.name, c]));
  globe
    .polygonCapColor((f) => colorFor(byName.get(f.atlasName)))
    .polygonAltitude((f) => (f.atlasName === state.country ? 0.024 : 0.006))
    .polygonStrokeColor((f) =>
      f.atlasName === state.country ? "#dcf7ed" : "#405362",
    )
    .labelColor((m) => colorFor(byName.get(m.name)));
}
function focusCountry(name) {
  if (!globe || listMode) return;
  const f = geometries.find((f) => f.atlasName === name);
  const point = f?.properties || regionMarkers.find((r) => r.name === name);
  if (point)
    globe.pointOfView(
      { lat: point.lat, lng: point.lng, altitude: 1.45 },
      reduced ? 0 : 650,
    );
}
function setMapMode(list) {
  listMode = list;
  $("country-list").hidden = !list;
  $("globe-stage").hidden = list;
  $("list-mode").setAttribute("aria-pressed", String(list));
  $("globe-mode").setAttribute("aria-pressed", String(!list));
  if (globe) {
    if (list) globe.pauseAnimation();
    else globe.resumeAnimation();
  }
}

function download(name, type, content) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportBrief() {
  const saved = Object.values(board);
  if (!saved.length) {
    toast("Save an article first.");
    return;
  }
  const content = `<!doctype html><html lang="en"><meta charset="utf-8"><title>OSINT Research Brief</title><style>body{font:16px/1.6 system-ui;max-width:850px;margin:40px auto;color:#17232d;padding:20px}h1{font-size:32px}article{padding:20px 0;border-bottom:1px solid #ddd;break-inside:avoid}small{color:#52606b}a{word-break:break-all}.ar{direction:rtl}.note{white-space:pre-wrap;border-left:3px solid #739688;padding-left:15px}button{padding:10px 20px}@media print{button{display:none}}</style><h1>Research brief</h1><p>Exported ${esc(new Date().toISOString())}. Source reporting and personal notes.</p><button onclick="window.print()">Print / Save as PDF</button>${saved
    .map((item) => {
      const a = item.article;
      return `<article><h2>${esc(a.title_en || a.title)}</h2>${a.title_en ? '<p class="ar" lang="ar">' + esc(a.title) + "</p><small>Machine-translated English headline</small>" : ""}<p>${esc(a.source)} · ${esc(a.country || "Unassigned")} · published ${esc(a.published_date || "date unavailable")}</p><p>Article escalation label: ${esc(a.escalation || "unknown")}. Snapshot: ${esc(item.snapshot)}.</p>${safeURL(a.url) ? `<a href="${esc(safeURL(a.url))}">${esc(a.url)}</a>` : ""}${item.note ? `<h3>Personal notes</h3><p class="note">${esc(item.note)}</p>` : ""}</article>`;
    })
    .join(
      "",
    )}<p>Escalation labels describe keyword signals in reporting; they are not safety ratings. Saved reporting may have changed since its snapshot.</p></html>`;
  download("osint-research-brief.html", "text/html;charset=utf-8", content);
  toast("Briefing downloaded. Open it to print or save as PDF.");
}

let tourStep = 0;
const tour = [
  [
    "Start with the bigger picture.",
    "Rotate the globe, or switch to List. Colors show the proportion of collected reporting labeled high escalation. Choose a country to see the reporting behind its color.",
  ],
  [
    "Compare the coverage.",
    "Add a country to comparison, then select another. Both use the same date, source, topic, and escalation filters. Counts show how much reporting supports each percentage.",
  ],
  [
    "Read both sides of the language barrier.",
    "Open a headline to inspect the Arabic original, machine translation, publisher, dates, and classification method. Follow the source link to read the original article.",
  ],
  [
    "Explore names, not just headlines.",
    "Entities groups attested Arabic spellings. With a complete archive, English translations and generated romanizations can also find their articles.",
  ],
  [
    "Leave with a useful finding.",
    "Save an article, add a note on your Research board, and export a printable, sourced brief. Notes stay in this browser; view links share only public filters.",
  ],
];
function renderTour() {
  const [t, p] = tour[tourStep];
  $("tour-step").textContent = `QUICK TOUR · ${tourStep + 1} / ${tour.length}`;
  $("tour-title").textContent = t;
  $("tour-text").textContent = p;
  $("tour-back").disabled = tourStep === 0;
  $("tour-next").textContent =
    tourStep === tour.length - 1 ? "Start exploring →" : "Next →";
}

document.addEventListener("click", (event) => {
  const el = event.target.closest("button");
  if (!el) return;
  const d = el.dataset;
  if (d.view) {
    state.view = d.view;
    apply();
  }
  if (d.country !== undefined) {
    $("detail-dialog").close();
    selectCountry(d.country);
  }
  if (d.topic) {
    state.topic = d.topic;
    apply();
  }
  if (d.clear) {
    state[d.clear] = "";
    apply();
  }
  if (d.detail) showArticle(d.detail);
  if (d.save) saveArticle(d.save);
  if (d.entity) showEntity(d.entity);
  if (d.entityFilter) {
    const entity = entities.find((e) => e.id === d.entityFilter);
    state = {
      ...parseState(""),
      view: "search",
      q: entity?.name || "",
      entity: d.entityFilter,
    };
    $("detail-dialog").close();
    apply();
  }
  if (d.nameSearch) {
    state = { ...parseState(""), view: "search", q: d.nameSearch };
    $("detail-dialog").close();
    apply();
  }
  if (d.uncompare) {
    compare = compare.filter((c) => c !== d.uncompare);
    apply();
  }
  if (d.day) {
    state.from = d.day;
    state.to = d.day;
    apply();
  }
  if (d.days !== undefined) {
    const days = Number(d.days);
    state.to = days
      ? new Date(snapshot.generated_at).toISOString().slice(0, 10)
      : "";
    state.from = days
      ? new Date(Date.parse(state.to + "T00:00:00Z") - (days - 1) * 86400000)
          .toISOString()
          .slice(0, 10)
      : "";
    apply();
  }
  if (el.id === "compare-add") {
    if (!compare.includes(state.country)) {
      if (compare.length === 3) {
        toast("Compare up to three countries. Remove one to add another.");
        return;
      }
      compare.push(state.country);
    }
    apply();
    toast("Added. Select another country on the globe or list.");
  }
  if (el.id === "compare-clear") {
    compare = [];
    apply();
  }
});
$("filters").addEventListener("submit", (event) => {
  event.preventDefault();
  state.view = "search";
  apply();
});
let debounce;
$("query").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    state.q = $("query").value;
    state.entity = "";
    apply();
  }, 180);
});
for (const [id, key] of [
  ["source", "source"],
  ["topic", "topic"],
  ["level", "level"],
  ["date-from", "from"],
  ["date-to", "to"],
])
  $(id).addEventListener("change", () => {
    state[key] = $(id).value;
    if (state.from && state.to && state.from > state.to) {
      toast("Start date must be on or before end date.");
      state[key] = "";
    }
    apply();
  });
$("reset").onclick = () => {
  state = { ...parseState(""), view: state.view };
  apply();
};
$("sort").onchange = () => render();
$("load-more").onclick = () => {
  if (state.view === "explore") {
    state.view = "search";
    apply();
  } else {
    pageSize += 20;
    renderArticles();
  }
};
$("list-mode").onclick = () => setMapMode(true);
$("globe-mode").onclick = () => setMapMode(false);
$("globe-reset").onclick = () =>
  globe?.pointOfView({ lat: 25, lng: 36, altitude: 1.75 }, reduced ? 0 : 650);
$("detail-close").onclick = () => $("detail-dialog").close();
$("method-open").onclick = showMethod;
$("map-info").onclick = showMethod;
$("share").onclick = async () => {
  try {
    await navigator.clipboard.writeText(location.href);
    toast("View link copied. Your research notes remain private.");
  } catch {
    openDialog(
      "SHARE THIS VIEW",
      `<p>Copy this link to share the current filters:</p><textarea class="saved-note" readonly>${esc(location.href)}</textarea>`,
    );
  }
};
$("saved-results").addEventListener("input", (event) => {
  if (event.target.dataset.note) {
    const key = event.target.dataset.note;
    if (board[key]) {
      board[key].note = event.target.value;
      persistBoard();
    }
  }
});
$("export-notes").onclick = () =>
  download(
    "osint-research-notes.json",
    "application/json",
    JSON.stringify(
      {
        version: 1,
        exported_at: new Date().toISOString(),
        items: Object.values(board),
      },
      null,
      2,
    ),
  );
$("export-brief").onclick = exportBrief;
$("tour-start").onclick = () => {
  tourStep = 0;
  renderTour();
  $("tour-dialog").showModal();
};
$("tour-close").onclick = () => $("tour-dialog").close();
$("tour-back").onclick = () => {
  tourStep--;
  renderTour();
};
$("tour-next").onclick = () => {
  if (tourStep === tour.length - 1) {
    $("tour-dialog").close();
    return;
  }
  tourStep++;
  renderTour();
};
$("demo-toggle").onclick = async () => {
  try {
    if (demo) {
      if (!currentSnapshot) {
        toast("Current reporting is unavailable. Reload to retry.");
        return;
      }
      demo = false;
      activate(currentSnapshot);
    } else {
      const data = await fetchJSON("assets/demo.json");
      demo = true;
      activate(data);
    }
    state = { ...parseState(""), view: "explore" };
    compare = [];
    apply();
  } catch {
    toast(
      "The example snapshot could not be loaded. Current reporting is still available.",
    );
  }
};
document.addEventListener("keydown", (event) => {
  if (
    event.key === "/" &&
    !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName) &&
    !$("detail-dialog").open &&
    !$("tour-dialog").open
  ) {
    event.preventDefault();
    $("query").focus();
  }
});
document.addEventListener("visibilitychange", () => {
  if (globe) {
    if (document.hidden || listMode || state.view !== "explore")
      globe.pauseAnimation();
    else globe.resumeAnimation();
  }
});
window.addEventListener("popstate", () => {
  state = parseState(location.search);
  apply();
});
try {
  currentSnapshot = await loadSnapshot();
  if (location.pathname.endsWith("high_risk.html"))
    state = { ...state, view: "search", level: "high" };
  if (location.pathname.endsWith("country.html")) {
    const slug = new URLSearchParams(location.search).get("c");
    state.country =
      currentSnapshot.index?.countries?.find((c) => c.slug === slug)?.country ||
      currentSnapshot.index?.countries?.find((c) => c.slug === slug)?.name ||
      state.country;
  }
  compare = (new URLSearchParams(location.search).get("compare") || "")
    .split(",")
    .filter((name) => currentSnapshot.articles.some((a) => a.country === name))
    .slice(0, 3);
  if (new URLSearchParams(location.search).get("demo") === "1") {
    demo = true;
    activate(await fetchJSON("assets/demo.json"));
  } else activate(currentSnapshot);
} catch (error) {
  $("load-status").textContent =
    "Reporting could not be loaded. Reload to retry, or open the example snapshot below.";
  $("load-status").classList.add("warning");
  $("briefing").innerHTML = empty(
    "Snapshot unavailable",
    "The original sources remain available from the existing console.",
  );
  $("globe-loading").textContent = "Waiting for reporting data.";
  console.error(error);
}
