# ADR 0027: Require a healthy terminal run before promotion

## Status

Accepted — 2026-08-21

## Context

ADR 0020 proves that a release candidate's manifest names immutable, verified
bytes. It did not require those bytes to be tied to a completed data run.
`begin_promotion` previously checked candidate registration and publication
high-water state only. A registered candidate could therefore be orphaned from
its run history, created while a run was still active, attached to a failed or
abandoned run, attached to a degraded run, or recorded in an order that did
not prove the run had finished its data work before candidate creation.

ADR 0026 now records run, stage, and source lifecycle facts in the ledger.
Those facts are enough to add a small pre-provider safety gate. They are not
enough to prove which exact artifact set a hosting provider currently serves;
that release-marker and live-observation work remains separate.

## Options considered

1. **Trust workflow ordering.** Rejected. A retry, a manual caller, or a
   damaged adopted ledger can bypass workflow intent even when the candidate
   bytes and manifest are valid.
2. **Build provider release markers and baseline capture first.** Deferred.
   Those are needed to prove an external deployment is live, but they do not
   answer whether a candidate came from a healthy completed data run.
3. **Gate `begin_promotion` on the candidate's existing ledger history —
   chosen.** This is a local, reversible check before mutable publication
   state or a provider call.

## Decision

Every normal promotion and rollback promotion must prove the candidate's own
run is eligible before `begin_promotion` can append `promotion_started`.
An operator-supplied run ID is still recorded for the promotion action, but it
cannot borrow health from a different run.

The guard requires all of the following:

- the registered candidate's run ID and commit SHA match its immutable
  manifest-derived candidate identity;
- the candidate run has one `run_started` event and one terminal event;
- that terminal event is `run_succeeded` with the same commit SHA;
- the candidate registration follows `run_started` and precedes that terminal
  success;
- the projected run health is exactly `healthy`, rejecting running, overdue,
  partial, failed, abandoned, and degraded runs;
- no ordinary stage or source boundary appears after candidate registration.
  When the existing runtime's `prepare_release` wrapper is present, candidate
  registration must be between its start and successful terminal boundary.

The final ordering rule does not add a fixed catalog of stages. It only proves
that a caller did not register a bundle and then continue ordinary data work.
This keeps the generic release layer usable for a future pipeline with a
different stage list while preserving the known `prepare_release` boundary.

The guard raises `CandidateRunNotEligible` before it creates or locks
`publication_state`, writes `promotion_started`, or reaches a deployment
adapter. It adds no event type, reason code, migration, workflow, provider,
Cloudflare, Worker, dashboard, or public-contract change.

## Failure and recovery behavior

- An ineligible candidate remains an immutable manifest and artifact set for
  inspection; rejection does not delete or rewrite ledger history.
- To publish new data, rerun the pipeline so candidate creation occurs after
  the data work and before its healthy terminal success, then start a new
  promotion.
- A rollback target must meet the same evidence rule. A legacy candidate with
  no trustworthy completed-run history is intentionally not a safe rollback
  target; rebuild and validate an equivalent candidate instead of bypassing
  the guard.
- If this checkpoint itself must be reversed, revert the code-only commit.
  No database schema or external deployment state was changed by the guard.

## Consequences

- A valid manifest alone is no longer enough to make deployment eligible;
  promotion now has durable evidence that the represented run finished
  healthily.
- Existing malformed, manually constructed, or historical candidates without
  this evidence cannot be promoted. That is a deliberate fail-closed tradeoff.
- Exact live-release proof, provider marker verification, baseline capture,
  and release reconciliation remain future work. This decision does not claim
  that any public URL serves a particular candidate.
