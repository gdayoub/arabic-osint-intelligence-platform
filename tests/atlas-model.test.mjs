import test from "node:test";
import assert from "node:assert/strict";
import {
  filterArticles,
  summarize,
  countriesFor,
  colorFor,
  dailyCounts,
  normalize,
  safeURL,
} from "../src/api/static/assets/atlas-model.mjs";
const articles = [
  {
    id: "old",
    country: "Egypt",
    source: "A",
    topic: "Politics",
    escalation: "high",
    title: "خبر",
    published_date: "2026-08-01T01:00:00Z",
    collected_at: "2026-09-07T12:00:00Z",
    entity_ids: ["e1"],
  },
  {
    id: "new",
    country: "Egypt",
    source: "B",
    topic: "Politics",
    escalation: "low",
    title: "News",
    published_date: "2026-09-07T22:00:00Z",
    entity_ids: [],
  },
  {
    id: "unknown",
    country: null,
    source: "A",
    escalation: null,
    title: "Undated",
    published_date: null,
    entity_ids: [],
  },
];
test("all-dates search includes unassigned and undated articles", () =>
  assert.equal(filterArticles(articles, {}).length, 3));
test("publication date, not late collection, controls the time window", () =>
  assert.deepEqual(
    filterArticles(articles, { from: "2026-09-07", to: "2026-09-07" }).map(
      (a) => a.id,
    ),
    ["new"],
  ));
test("country comparison omits only country while preserving other filters", () =>
  assert.deepEqual(
    filterArticles(
      articles,
      { country: "Iran", source: "A", level: "high" },
      [],
      true,
    ).map((a) => a.id),
    ["old"],
  ));
test("English alias reaches Arabic entity article without title match", () =>
  assert.deepEqual(
    filterArticles(articles, { q: "mohammed" }, [
      {
        id: "e1",
        name: "محمد",
        romanizations: ["mohammed"],
        surface_forms: [],
      },
    ]).map((a) => a.id),
    ["old"],
  ));
test("unknown escalation is not low and not in classified denominator", () => {
  const s = summarize(articles);
  assert.equal(s.levels.unknown, 1);
  assert.equal(s.levels.low, 1);
  assert.equal(s.highShare, 0.5);
});
test("empty data differs from zero high-escalation share", () =>
  assert.notEqual(colorFor(summarize([])), colorFor(summarize([articles[1]]))));
test("country summary and contributing articles agree", () =>
  assert.equal(
    countriesFor(articles)[0].total,
    filterArticles(articles, { country: "Egypt" }).length,
  ));
test("daily counts do not convert undated articles to epoch", () =>
  assert.deepEqual(dailyCounts(articles), [
    ["2026-08-01", 1],
    ["2026-09-07", 1],
  ]));
test("comparison normalization leaves original text untouched", () => {
  const original = "إِيـرَان";
  assert.equal(normalize(original), "ايران");
  assert.equal(original, "إِيـرَان");
});
test("source URLs cannot execute scripts or embed credentials", () => {
  assert.equal(safeURL("javascript:alert(1)"), "");
  assert.equal(safeURL("https://user:pass@example.com"), "");
  assert.equal(safeURL("https://example.com/a"), "https://example.com/a");
});
