# ADR 0036: Save M3 reports with complete evidence and explicit fixture limits

## Status

Accepted — 2026-10-12

## Context

M3 already has exact-span/type micro scoring, per-type metrics and a console
comparison. The terminal shows only a few disagreements and omits source
offsets and input fingerprints. That makes a copied score hard to audit and
easy to mistake for production accuracy. The current evaluation corpus is
authored golden text, with no verified real-source sample or holdout design.

The project also already has immutable JSON serialization and stricter M4
learning-evidence gates. Adding a second scoring implementation or silently
weakening those gates would be the wrong response to a presentation gap.

## Options considered

1. **Keep console output only.** Smallest change, but leaves incomplete error
   evidence and no durable connection between a score and its inputs.
2. **Build a generic benchmark platform or new M4 framework.** Rejected as
   unnecessary scope and a risk of conflicting definitions and evidence gates.
3. **Add an optional evidence export to the existing checkpoint.** Chosen.
   Reuse the evaluator unchanged and the existing sealed-JSON writer, with a
   separate, explicitly M3-specific report schema.
4. **Treat the golden corpus as a production sample.** Rejected: a filename
   or score cannot establish sampling, independent annotation or holdout.
5. **Change production model error handling to support this report.** Rejected
   for this checkpoint. A temporary error-log observer suffices to distinguish
   observed model failures from ordinary empty output without changing it.

## Decision

Add optional `--out` to `scripts/m3_checkpoint.py`, retaining console-only
operation and optional-model behavior. Fix the direct-script import path so
the documented command works without `PYTHONPATH` configuration.

Reports contain every original fixture document, validated Unicode span,
prediction and error; per-type and aggregate metrics; extractor versions and
settings; exact dataset/configuration/evaluation-code fingerprints; Git base
and relevant dirty state; runtime versions; and a canonical content digest.
The writer excludes timestamps and machine-local paths and refuses to replace
a report with changed content. An input-file change during evaluation blocks
saving. No new library, service, database table or deployment step is added.

Keep the evaluator's distinct `(start, end, type)` matching semantics. Preserve
raw prediction duplicates for inspection but deduplicate errors by the same
identity so the lists agree with the metric counts. Reject invalid spans
before scoring rather than persisting a confident but untraceable result.

All reports permanently identify this corpus as an authored golden regression
fixture with insufficient real-world evidence. Skipped, unavailable and failed
model runs are explicit, never implied zero-quality results. Observed inference
failures leave diagnostic counts intact, suppress a comparison verdict and
produce a nonzero exit. Best-effort model revision metadata is not represented
as a pinned model or a weight-file digest.

## Consequences

- Saved results can be independently inspected and compared without losing
  original text or offsets to the console's disagreement limit.
- Clean versus modified evaluation code is visible. Changing code, inputs,
  Git revision or runtime metadata requires a new report filename.
- The script imports only the existing content-hash/writer helpers from the
  M4 learning module. M4 scoring, sampling and deployment gates remain separate
  and unchanged. A shared utility extraction can be considered later if more
  report producers need it; this change does not duplicate the serializer.
- Reports contain the public fixture's complete text. Any future real-source
  evaluation needs its own data/provenance and sharing policy, plus labels
  capable of identifying repeated mentions by explicit offsets.
- No production accuracy or improvement claim follows from this checkpoint.
  No actual transformer result is claimed by tests that use fake outputs.
