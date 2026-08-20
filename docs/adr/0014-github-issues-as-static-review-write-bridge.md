# ADR 0014: GitHub issues as the static review write bridge

## Status

Accepted — 2026-08-20

## Context

The entity resolver writes uncertain mention pairs to an append-only review
queue, but the deployed dashboard is static.  It has no trusted server process
that can accept a decision and safely hold the Neon database credential.
Putting that credential, or an admin token that can reach it, in dashboard
JavaScript would make it public.

The project owner still needs a visible path from evidence to a durable
``same`` or ``different`` constraint.  The path must preserve reviewer
identity, provenance, retries, and the free/scale-to-zero deployment model.

## Decision

The baked ``data.json`` includes unresolved review pairs as an additive
``review_queue.items`` field.  Each item contains only public evidence: the
stored mention, article title, source, URL, score, threshold, and feature
snapshot.  Document bodies remain outside the public artifact.

Each dashboard decision link opens a prefilled GitHub issue with a strict title:

```
[entity-review] pair <positive integer> <accept|reject>
```

An ``issues: opened`` GitHub Actions workflow applies the command only when
``github.actor == github.repository_owner``.  Python parses the title with a
full-match regular expression; issue text is never interpolated into a shell
command.  The workflow writes the existing append-only resolution decision to
Neon, rebuilds and deploys the static snapshot, comments, and closes the issue.
The repository issue number is an idempotency key so a workflow rerun cannot
append the same answer twice.

The decision is available to the resolver immediately, but the graph applies
it on the next scheduled resolution run.  This keeps the decision workflow
short and avoids racing the six-hour full-corpus pipeline.

## Alternatives considered

### A public Worker API with a shared admin secret

Rejected.  A secret embedded in the dashboard is not a secret, while asking
the owner to paste one into a browser adds secret storage and rotation work.

### A separate authenticated web application

Rejected for this milestone.  It adds an identity provider, server lifecycle,
and another deployment surface for one owner.  GitHub already supplies the
required authenticated identity and audit log.

### CLI-only review

Kept as a fallback but rejected as the primary interface.  It made the feature
technically present and practically invisible, with no adjacent article
evidence for a quick judgment.

## Consequences

- The dashboard exposes a usable review queue without exposing write secrets.
- Every click still requires submitting the prefilled GitHub issue, making the
  write explicit and auditable.
- Only the repository owner can apply decisions through this workflow.
- The queue refreshes after each decision; entity clusters refresh on the next
  scheduled resolver run.
- If GitHub Issues or Actions are unavailable, the existing review CLI remains
  available.
