# ADR 0011: No LLM summarization in the scheduled core pipeline

## Context

The legacy pipeline (`src/processing/processing_pipeline.py`) calls
`generate_summary()` (`src/processing/ai_summarizer.py`, Claude Haiku) for
every medium/high-escalation article, inside the open database transaction,
serially, for potentially hundreds of articles per run. `process_core.py`
(M1.5 Stage 3b) is the new classification pipeline and deliberately does not
port this over.

## Decision

`process_core.py` writes `topic`, `escalation`, and `country` facts only. No
LLM call anywhere in the scheduled pipeline. `bake_dashboard_data.py`
hardcodes `"ai_summary": None` in every recent-article entry rather than
trying to source one.

## Consequences (reasons, really — all three independently sufficient)

- **The brief's own constraint**: `docs/AGENT_BRIEF.md` §4 states no LLM API
  for extraction until a non-LLM baseline is built and measured.
  `process_core.py`'s rule-based classifiers *are* that baseline — using an
  LLM in the same pipeline that's supposed to establish the baseline would
  defeat the point.
- **A real defect, not carried forward**: the legacy call happens serially,
  inside an open DB transaction, for every qualifying article — on a
  scale-to-zero database (Neon) over what could be a multi-minute window,
  that's a long-held transaction for no good reason. Removing the call
  removes the defect; it wasn't worth porting the bug just to preserve the
  feature.
- **No extractor version recorded**: `ai_summary` is written straight to a
  column with no record of which model or prompt produced it — a P4
  violation in the legacy code that the new schema's discipline (every
  extractor registered and versioned) doesn't have a clean equivalent for
  without redesigning the call it's attached to.

## If summaries come back later

Not never — just not automatic, and not inside this pipeline. The shape
that would satisfy P1/P4/P7:
- A separate, opt-in step (e.g. `main.py summarize-core --max-summaries N`),
  never part of the default scheduled run.
- Calls made **outside** any open DB session — collect document ids, close
  the session, call the API, reopen a session to write results.
- Written as `facts` with `fact_type="ai_summary"` under a registered
  extractor version (`claude-haiku-summarizer vX.Y.Z`), so a prompt or model
  change is a traceable version bump, not a silent behavior change.
