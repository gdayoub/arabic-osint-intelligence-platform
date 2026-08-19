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

## 2026-08-19 — M3 mention extraction: gazetteer vs transformer

**Method:** `scripts/m3_checkpoint.py` against `tests/golden/ner_eval.json`
(15 documents, 36 gold spans, hand labelled). Exact span match — predicted
start, end and type must all equal a gold one. Micro averaged.

| extractor | P | R | **F1** |
|---|---|---|---|
| `gazetteer_extractor` v1.0.0 (97 patterns) | 0.94 | 0.86 | **0.90** |
| `camelbert_ner_extractor` v1.0.0 | 0.89 | 0.89 | **0.89** |

Per type:

| type | gazetteer F1 | model F1 |
|---|---|---|
| person | 0.91 | **1.00** |
| location | **0.92** | 0.87 |
| organization | 0.84 | 0.84 |

**The aggregate is a tie and the aggregate is misleading.** The per-type
split is the real finding:

- **The model is perfect on people.** It found `جان نويل بارو` and
  `مسرور بارزاني`, neither of which is in the gazetteer. That is the whole
  argument for a model — people are an open set and a dictionary cannot
  enumerate them.
- **The gazetteer wins on locations.** Countries and cities are a closed
  set that a list handles perfectly, and the model invents locations from
  adjectival usage (`وفدا روسيا` → tagged `روسيا`).
- **Both are equally mediocre on organizations**, for different reasons.

**A bug found by measuring, not by reading.** The first run scored the model
at 0.83 with person F1 0.77. `aggregation_strategy="simple"` merges
consecutive same-tag tokens but does not group subword pieces into words
first, so `مسرور بارزاني` came back as `مس` + `رور بارزاني` — two people
instead of one. Switching to `"average"`, which groups word pieces before
aggregating, took the model from 0.83 to 0.89 and person F1 from 0.77 to
1.00. Nothing about the model changed. One argument did.

**Decision:** the scheduled pipeline keeps the gazetteer. A 2GB dependency
and minutes of CI per run is not justified by −0.01 F1 overall. The honest
next step is not "pick one" but ensemble them — union the spans, since their
failure modes are close to complementary (model finds unknown people,
gazetteer holds the closed location set). That is worth measuring before
M4 depends on the output.
