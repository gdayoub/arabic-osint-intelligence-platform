# Benchmark results

Per P7: no performance-relevant change lands without a before/after number
recorded here.

## 2026-08-17 — gzip compression on Arabic article text (M1.5, ADR 0005)

**Method:** `scripts/bench_compression.py`, 5 hand-written Arabic paragraphs
(politics/economy/military/protests/humanitarian — the topics the keyword
classifier already targets), 978-1342 bytes UTF-8 each. `compress_text()`
from `src/store/blob.py` (gzip level 6, deterministic `mtime=0`) vs raw
`gzip.compress(level=9)` for comparison.

**Caveat, stated up front:** these are single-paragraph excerpts. A real
scraped article body (`src/scraping/*_scraper.py`) runs ~3,000-5,000 Arabic
characters across several paragraphs — longer text gives gzip's LZ77 window
more repetition to find, so production numbers should compress *better*
than this. This run exists to unblock the gzip-vs-zstd decision now, not to
be the final word — re-run against real ingested corpus after M1.5 Stage 4
has been live for a few days, and update this entry rather than replacing it
(P5: superseded, not overwritten — see the entry below when that happens).

**Results:**

| body | raw bytes | gzip-6 bytes | ratio | gzip-9 bytes | ratio | gzip-6 time |
|---|---|---|---|---|---|---|
| 1 (politics) | 1342 | 581 | 2.31x | 581 | 2.31x | 0.368 ms |
| 2 (economy) | 1218 | 543 | 2.24x | 543 | 2.24x | 0.121 ms |
| 3 (military) | 1069 | 481 | 2.22x | 481 | 2.22x | 0.132 ms |
| 4 (protests) | 978 | 457 | 2.14x | 457 | 2.14x | 0.050 ms |
| 5 (humanitarian) | 1034 | 472 | 2.19x | 472 | 2.19x | 0.052 ms |
| **total** | **5641** | **2534** | **2.23x** | **2534** | **2.23x** | **0.723 ms** |

- Average: 1128 bytes raw -> 507 bytes compressed, **0.145 ms/doc**.
- **gzip-6 and gzip-9 produced byte-identical output on every sample.** At
  this input size the higher search effort of level 9 found nothing extra —
  the level 6 default is not leaving compression on the table here, so the
  "level 9 costs ~2x CPU for ~1-2%" tradeoff from the ADR doesn't even
  apply at this size. Confirms level 6 is the right default with no loss.
- At 500-1000 documents/pipeline-run (4 sources x ~120/source), 0.145 ms/doc
  compression time is ~72-145 ms total — not a measurable fraction of an
  8-15 minute scrape run.

**Conclusion for ADR 0005:** gzip clears the bar. Even the conservative 2.2x
ratio on short excerpts, against a target of a few MB/run into a 10 GB free
tier, means storage is not the constraint — dependency simplicity (stdlib,
no new package on Python 3.11) is the deciding factor, not compression
ratio. zstd was not installed or measured, consistent with the "don't add a
dependency to solve a problem you don't have yet" rule — if a future
measurement on real corpus shows gzip is a real bottleneck (unlikely at this
volume), that's the trigger to benchmark zstd for real, not this entry.
