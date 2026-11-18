export const geographyAliases = {
  "United States of America": "United States",
  "United Arab Emirates": "UAE",
};
export const regionMarkers = [
  { name: "Gaza", lat: 31.42, lng: 34.37, regional: true },
  { name: "Bahrain", lat: 26.07, lng: 50.55, regional: false },
];
export function normalize(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\u064B-\u065F\u0670\u0640]/g, "")
    .replace(/[أإآٱ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه");
}
export function safeURL(value) {
  try {
    const u = new URL(value);
    return ["https:", "http:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? u.href
      : "";
  } catch {
    return "";
  }
}
export function dateMillis(article) {
  return article.published_date ? Date.parse(article.published_date) : NaN;
}
export function filterArticles(
  articles,
  state,
  entities = [],
  omitCountry = false,
) {
  const query = normalize(state.q).trim();
  const matched = new Set(
    entities
      .filter((e) =>
        normalize(
          [
            e.name,
            e.name_en,
            ...(e.surface_forms || []),
            ...(e.romanizations || []),
          ].join(" "),
        ).includes(query),
      )
      .map((e) => e.id),
  );
  return articles.filter((a) => {
    const time = dateMillis(a);
    if (
      state.from &&
      (!Number.isFinite(time) || time < Date.parse(state.from + "T00:00:00Z"))
    )
      return false;
    if (
      state.to &&
      (!Number.isFinite(time) ||
        time >= Date.parse(state.to + "T00:00:00Z") + 86400000)
    )
      return false;
    if (
      !omitCountry &&
      state.country &&
      (a.country || "Unassigned") !== state.country
    )
      return false;
    if (state.source && a.source !== state.source) return false;
    if (state.topic && a.topic !== state.topic) return false;
    if (state.level && a.escalation !== state.level) return false;
    if (state.entity && !(a.entity_ids || []).includes(state.entity))
      return false;
    return (
      !query ||
      normalize(
        [a.title, a.title_en, a.country, a.topic, a.source].join(" "),
      ).includes(query) ||
      (a.entity_ids || []).some((id) => matched.has(id))
    );
  });
}
export function summarize(articles) {
  const levels = { high: 0, medium: 0, low: 0, unknown: 0 },
    sources = new Set(),
    topics = Object.create(null);
  for (const a of articles) {
    levels[Object.hasOwn(levels, a.escalation) ? a.escalation : "unknown"]++;
    sources.add(a.source);
    topics[a.topic || "Uncategorized"] =
      (topics[a.topic || "Uncategorized"] || 0) + 1;
  }
  const classified = levels.high + levels.medium + levels.low;
  return {
    total: articles.length,
    levels,
    sources: sources.size,
    highShare: classified ? levels.high / classified : null,
    topics: Object.entries(topics).sort((a, b) => b[1] - a[1]),
  };
}
export function countriesFor(articles) {
  const groups = new Map();
  for (const a of articles) {
    if (!a.country) continue;
    if (!groups.has(a.country)) groups.set(a.country, []);
    groups.get(a.country).push(a);
  }
  return [...groups]
    .map(([name, rows]) => ({ name, ...summarize(rows) }))
    .sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
}
export function colorFor(s) {
  if (!s || s.highShare === null) return "#25313c";
  const t = Math.sqrt(s.highShare),
    stops = [
      [52, 131, 120],
      [234, 179, 83],
      [236, 92, 81],
    ],
    index = t < 0.5 ? 0 : 1,
    local = index ? (t - 0.5) * 2 : t * 2;
  return (
    "#" +
    stops[index]
      .map((v, i) =>
        Math.round(v + (stops[index + 1][i] - v) * local)
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}
export function dailyCounts(articles) {
  const days = new Map();
  for (const a of articles) {
    const time = dateMillis(a);
    if (!Number.isFinite(time)) continue;
    const day = new Date(time).toISOString().slice(0, 10);
    days.set(day, (days.get(day) || 0) + 1);
  }
  return [...days].sort((a, b) => a[0].localeCompare(b[0]));
}
export function parseState(search) {
  const p = new URLSearchParams(search),
    state = {};
  for (const key of [
    "q",
    "country",
    "source",
    "topic",
    "level",
    "from",
    "to",
    "view",
  ])
    state[key] = p.get(key) || "";
  if (!["explore", "search", "entities", "saved"].includes(state.view))
    state.view = "explore";
  for (const key of ["from", "to"])
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(state[key]) ||
      !Number.isFinite(Date.parse(state[key]))
    )
      state[key] = "";
  if (!["", "high", "medium", "low"].includes(state.level)) state.level = "";
  return state;
}
