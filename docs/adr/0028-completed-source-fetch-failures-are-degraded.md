# ADR 0028: Treat completed scraper fetch failures as degraded data coverage

## Status

Accepted — 2026-08-21

## Context

The ledgered scheduled pipeline originally treated every
`source_fetch_failed` reason as a failed source.  That conflated two different
conditions:

- a scraper completed normally and returned its closed telemetry after one or
  more listing or article requests failed (for example a temporary `403`);
- a scraper raised unexpectedly, the source transaction or commit failed, or
  telemetry could not be validated.

The first condition has a useful, durable partial-data result.  The second has
no safe completion claim and must remain fail-closed.  In production, a
temporary source block stopped processing, extraction, baking, and the
existing direct deployment even when the other configured sources had
completed successfully.

## Decision

`SOURCE_FETCH_FAILED` is an allowed **degraded completion** only when it is
returned by the completed scraper telemetry boundary with a valid, closed
source result.  It retains that exact safe reason in the source-success event.
It permits later data stages—including the existing dashboard bake and direct
deployment step—only when at least one configured source yielded one or more
articles in the same run.

Zero yielded articles do not replace this reason.  A source blocked for every
attempt therefore remains visibly `source_fetch_failed`, rather than being
misreported as a generic zero-yield source.

Independently, ingestion has an all-sources coverage gate.  When every
configured source yields zero articles, it records the completed per-source
observations but fails the ingest stage and run with the closed
`source_zero_yield` reason before process, extract, resolve, translate, or
bake begins.  This applies regardless of whether the individual zero-yield
reasons were fetch, selector, parser, or ordinary empty-result observations.
It prevents a new generated timestamp from making an empty collection look
like fresh data.  Positive yield with zero inserts remains valid, because a
duplicate-only scrape still proves current source coverage.

The following paths are deliberately unchanged and fail closed:

- an exception escaping `scrape()`;
- a document/metadata transaction or commit failure;
- malformed counters, timestamps, statuses, or reason codes;
- an explicit `SourceOutcome(status="failed", ...)`, including one whose
  reason happens to be `source_fetch_failed`.

The candidate guard remains unchanged: a degraded run cannot create a release
candidate or begin promotion.  This decision only preserves useful static
data coverage for the already-existing direct deployment path; it makes no
claim that a release candidate is healthy or live.

## Failure and recovery behavior

- A temporary source fetch block is visible in the immutable ledger and
  public-safe health projection.  The remaining data stages run only when the
  run also has at least one yielded article from a configured source.
- An all-zero source run closes failed before baking, so its generated-at
  value cannot be directly deployed as evidence of current data.
- An unexpected scraper or storage failure records a failed source/stage/run
  and stops the bake, so the direct deployment step is skipped.
- To restore the earlier strict behavior, revert this code-only checkpoint.
  Historical ledger events remain append-only evidence and no schema or
  public-contract rollback is required.

## Consequences

- A single known upstream availability problem no longer suppresses an
  otherwise usable snapshot.
- Operators can distinguish coverage degradation from an unsafe ingestion
  execution failure without exposing HTTP bodies, URLs, credentials, or
  exception text.
- Release-candidate and promotion safety remain stricter than the direct
  deployment path: only fully healthy runs are eligible.
