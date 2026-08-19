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

## 2026-08-19 — M4 blocking and pair scoring

**Blocking arithmetic on the live corpus.** 4,589 mentions means
4589 x 4588 / 2 = **10,527,166** pairs if every pair is compared. At a
realistic 50 microseconds per scored pair that is about nine minutes for
354 articles, and it grows quadratically while the corpus grows linearly.
Multi-key blocking (last token, first+last initial, sorted token set,
character trigrams) only scores pairs that share a key.

Reduction ratio is reported alongside **pair completeness** deliberately.
Reduction ratio alone is gameable: dropping every pair scores 1.0 and finds
nothing. Both numbers or neither.

**Pair scorer, learned not hand tuned.** Logistic regression over six
features, 51 hand labelled pairs, leave-one-out cross validation because 51
rows is far too few for a held out split.

| | |
|---|---|
| leave-one-out AUC | **0.935** |
| threshold chosen | 0.60 |
| precision at 0.60 | 1.00 |
| recall at 0.60 | 0.59 |

Threshold is biased above the F1-maximising point on purpose. A wrong merge
fuses two real people, is nearly invisible afterwards, and is painful to
undo. A missed merge just leaves two entities for the review queue.

Learned weights:

| feature | weight |
|---|---|
| key_overlap | +2.915 |
| same_source | −0.427 |
| name_similarity | −0.387 |
| temporal_proximity | +0.120 |
| co_mention_overlap | +0.114 |
| same_type | 0.000 |

**A label leak I caused and had to fix.** The first run scored **AUC 1.000**
with `same_source` at +2.017 and `name_similarity` at −0.013 — the model
had learned to ignore the name entirely. Cause: `build_dataset` assigned
source and date *from the label*, giving positives a shared source one day
apart and negatives different sources twenty days apart. The label was
sitting in the feature vector under another name. Context features are now
drawn from a seeded RNG that never sees the label, the three uninformative
features correctly learned weights near zero, and AUC fell to a believable
0.935. **An AUC of exactly 1.000 on a real task is a bug report.**

`name_similarity` landing negative is not a glitch. Conditional on blocking
keys already overlapping, extra raw string similarity is mild evidence of a
*confusable* pair — حسن vs حسين scores 0.933 while the same-person pair
بشار الأسد vs بشار الاسد scores 0.96. The model learned that trap.
