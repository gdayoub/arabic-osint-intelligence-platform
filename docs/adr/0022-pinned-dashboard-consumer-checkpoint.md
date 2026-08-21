# ADR 0022: Pin dashboard consumers at exact revisions

## Status

Accepted.

## Context

The dashboard JSON is produced in this repository but consumed by the
separately deployed portfolio. A producer test can prove that its own bundle
is valid, and a public smoke test can prove that the current deployments are
reachable, but neither proves that a proposed producer contract is compatible
with a known portfolio revision.

Checking the portfolio's moving `main` branch would not be reproducible. It
would also let unrelated portfolio changes change the result of an old
producer commit. Reading the checkout repository and revision dynamically
from a pull-request-editable lock would let a pull request redirect CI into
running package scripts from another repository.

## Decision

The producer owns a consumer checkpoint lock under
`contracts/dashboard/consumers/`. The lock pins the portfolio's full commit,
contract version, manifest digest, bundle digest, schema version, public
route, full Vercel URL, and Cloudflare data URL.

The portfolio repository is private, so the public producer repository's
default Actions token cannot read it. The workflow uses a dedicated read-only
portfolio deploy key stored as the producer's `PORTFOLIO_DEPLOY_KEY` secret.
It is limited to that repository and is skipped for forked pull requests,
which do not receive repository secrets. The compatibility workflow checks
out the portfolio repository and revision as reviewed literals, then verifies
that:

- the checked-out Git revision is the pinned full commit;
- the consumer lock agrees with the producer checkpoint;
- the manifest and every vendored artifact are byte-for-byte identical;
- `/osint-dashboard.html` still exists at the expected public-file path;
- both the static dashboard and portfolio API route still use the unchanged
  Cloudflare `data.json` URL; and
- the portfolio's OSINT contract test and production build pass.

The focused test plus build are proportionate because this workflow runs only
when dashboard-contract inputs change. It does not edit portfolio UI files or
publish either repository.

## Consequences

A contract rollout is deliberately two-step: update and verify the consumer,
then advance the producer's pinned consumer revision. A mismatch fails before
deployment with a stable diagnostic. Updating the consumer revision requires
reviewing both the JSON lock and workflow checkout literal, making execution
of third-party package scripts an explicit code-review decision.

This adds CI time for `npm ci` and the portfolio build, but it replaces an
implicit cross-repository dependency with an auditable, reversible checkpoint.
Revoking the deploy key from the portfolio and deleting the producer secret
immediately removes the extra read access.
