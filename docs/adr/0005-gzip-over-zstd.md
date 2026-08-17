# ADR 0005: gzip over zstd for document text compression

## Context

Document text (ADR 0004) is compressed before being written to blob storage.
Two realistic choices: `gzip`, in the Python standard library, or `zstd`,
which is faster and typically compresses natural-language text slightly
better, but is not in the standard library on Python 3.11 (it lands in
stdlib as `compression.zstd` in Python 3.14).

## Options considered

1. **zstd**, via the third-party `zstandard` package.
2. **gzip**, stdlib, no new dependency.

## Decision

gzip. Per the project's "no dependency without justification" rule
(`docs/AGENT_BRIEF.md` §0.5): zstd's advantages are ~5x compression speed
and modestly better ratio on small text — and neither is a bottleneck this
project has. `benchmarks/results.md` (2026-08-17 entry) measured
`compress_text()` (gzip level 6) at **0.145 ms/document average** on
representative Arabic article text — at 500-1000 documents per pipeline
run, that's ~72-145 ms of compression time inside an 8-15 minute scrape run.
Not measurable against the run's own duration, let alone worth a new
dependency to shave further.

The measured compression ratio was **2.23x** on the sample bodies (short,
single-paragraph excerpts — see the benchmark entry's caveat that full
multi-paragraph article bodies should compress somewhat better, to be
re-measured against real corpus). Against a 10 GB R2 free tier and an
expected few-MB-per-run volume, storage headroom is not the constraint
either way — a 10-20% better ratio from zstd wouldn't change what tier
anything fits in.

`compress_text()`/`decompress_text()` also fix gzip's default
non-determinism: plain `gzip.compress()` embeds the current time in its
header, so the same input produces different output bytes each call. Level
6 (the default) is used, not 9 — the benchmark found gzip-6 and gzip-9
produced **byte-identical output** on every sample at this text length, so
level 9's extra search effort bought literally nothing here.

## Consequences

- One fewer dependency to install, explain, and keep patched.
- Slightly larger blobs and slightly slower compression than zstd would
  give — both immaterial at current and reasonably foreseeable volume.
- **Exit condition, stated so it isn't forgotten:** adopt `zstandard` the
  first time a real `benchmarks/results.md` entry shows compression time or
  storage cost is an actual bottleneck — not speculatively. Free exit: once
  the project moves to Python 3.14, `compression.zstd` is stdlib and the
  "new dependency" objection disappears on its own.
