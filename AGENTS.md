# handoff notes

i am george. this file is me briefing whoever picks this up next so you do
not have to rediscover things i already paid for. read `docs/AGENT_BRIEF.md`
first for the actual spec, then this for where things stand and what bit me.

## how i want to work

this is in `docs/AGENT_BRIEF.md` section 0 in full but the short version:

- i am building this to learn and to defend it in interviews. if you write
  code i cannot explain then the project failed even if the code works.
- explain before you build. tell me the approach and what you rejected.
- stop at checkpoints. do not chain three milestones together.
- teach me the python. i know algorithms. idioms are my weak side.
- boring code over clever code.
- no dependency without justifying it. i would rather write 40 lines.
- write an ADR in `docs/adr/` for every real decision. those are my
  interview notes.

if i say just build it then go, but tell me what you skipped.

## what this is

an entity resolution and link analysis engine. arabic OSINT is the first
domain but the engine is not supposed to be arabic specific. language and
object types are config and not python. that is the single biggest
architectural constraint.

the shape is `document -> mention -> entity -> graph`.

## where it runs. all free.

```
github actions (cron every 6h)
  -> scrapes al jazeera, bbc arabic, cnn arabic
  -> text into cloudflare R2, gzipped, content addressed
  -> metadata into neon postgres
  -> classify, extract mentions, resolve entities, translate titles
  -> bakes data.json
  -> deploys static dashboard to a cloudflare worker
```

live: https://arabic-osint-dashboard.georgedayoub500.workers.dev

it also feeds my portfolio at
https://george-dayoub-portfolio.vercel.app/osint-dashboard.html which is a
separate repo (`gdayoub/george-portfolio`). that page and a react widget on
the homepage both pull the same data.json cross origin. if you change the
data.json shape you break both.

nothing is always on. no server to pay for. secrets live in github actions
secrets, not in the repo.

## what is done

- **M1** data model and provenance. every fact traces to a document and a
  character span plus an extractor name and version.
- **M1.5** hosting and storage. i added this milestone myself, it was not in
  the original brief. moved off railway because it wanted money.
- **M2** language adapters. `src/lang/`. arabic and english behind one
  protocol.
- **M3** mention extraction. hand written aho corasick gazetteer plus a
  camelbert NER wrapper. gazetteer F1 0.90, model 0.89.
- **M4** entity resolution. blocking, learned pair scorer, union find with a
  complete linkage guard. core is done, review queue is not.

current live numbers: 451 documents, 6084 mentions, 61 entities.

## things that bit me. do not re-learn these.

**normalization must never touch stored text.** it shortens the string so
every mention offset i stored points somewhere else and P2 dies. normalize
for comparison only. the legacy `processed_articles.cleaned_text` is this
mistake preserved in amber, go look at it.

**the block size guard ate my best entities.** `إيران` appears 600+ times,
all identical, so blocking made one huge block, the block blew past
max_block_size and got dropped as oversized. my most mentioned entities were
the most likely to be skipped. fix was collapsing byte identical strings
BEFORE blocking. 3769 entities became 87.

**i was rediscovering what my own config already said.** `config/gazetteer.yaml`
lists ترامب and ترمب as aliases of دونالد ترامب and resolution was ignoring
that and trying to fuzzy match its way there, which fails because the two
share no trigrams. now known names group by the gazetteer canonical and the
scorer only handles what the dictionary does not know.

**AUC 1.000 is a bug report and not a win.** my first pair scorer scored a
perfect AUC because i built the training features from the label. positives
got the same source one day apart and negatives got different sources twenty
days apart, so the model read the answer off `same_source` and never looked
at the name. fixed by drawing context from a seeded RNG. AUC fell to 0.935
which is believable.

**the transformer wrapper split names in half.** `aggregation_strategy="simple"`
merges same tag tokens but does not group subword pieces into words first, so
مسرور بارزاني came back as two people. `"average"` fixes it. that one argument
was worth 0.06 F1.

**chardet reads arabic UTF-8 as cyrillic.** the scraper preferred the guess
over the declared charset and stored a headline as mojibake, which deepl then
faithfully translated into "shu shu shu shu". `_resolve_encoding` in
`src/scraping/base_scraper.py` trusts the header first now.

**tests being green means nothing here.** every one of the bugs above was
found by looking at real output. the suite was at 245 passing while
resolution was producing garbage in production.

## known problems i have not fixed

- **the learned scorer does nothing right now.** last run matched 0 pairs.
  every merge came from exact duplicate collapsing and a dictionary lookup,
  neither of which is ML. the AUC number is real but it is not doing work in
  production and i should not claim it on a resume until it is.
- **the training set is drawn wrong.** 17 of 24 negatives in
  `tests/golden/pair_labels.json` do not share a blocking key, so they are
  pairs the scorer never sees at inference. i need pairs sampled from actual
  blocked candidates. i can label arabic name pairs fast, so ask me.
- 51 training pairs is thin. AUC 0.935 on leave one out is honest but fragile.
- threshold 0.60 gives precision 1.00 and recall 0.59. i chose precision on
  purpose because a bad merge is nearly invisible later, but 40% of true
  merges are missed and the review queue is meant to catch those.
- the gazetteer is ~100 names. it cannot find anyone not on the list. that is
  the whole argument for the model and why the ensemble is worth measuring.
- `src/dashboard/app.py` (streamlit) and `src/api/main.py` (fastapi) are dead
  code. frozen, not deployed, see ADR 0010.

## next

per the brief, in order:

1. finish M4. the human review queue for pairs near the threshold, and manual
   merge and split recorded with provenance.
2. retrain the scorer on blocking surviving pairs. bigger label set.
3. M5 bilingual. translation exists for titles already. entity names and
   evidence sentences plus bidirectional search is the rest.
4. M6 links and the graph. co-occurrence edges, recursive CTEs, centrality.
   this is where country pages stop being a `GROUP BY` and start being real.

## running it

```bash
python -m pytest -q                      # 251 passing, 2 xfail
python main.py --help                    # every pipeline step

python scripts/m2_checkpoint.py          # arabic normalization table
python scripts/m3_checkpoint.py          # gazetteer vs model F1. needs requirements-ml.txt
python scripts/train_pair_scorer.py      # refits config/pair_scorer_weights.json
```

`requirements.txt` is what the pipeline installs and it is deliberately light.
`requirements-ml.txt` (torch, transformers, sklearn) is for local evaluation
and training only. do not let torch into the scheduled pipeline, it is 2GB
and buys −0.01 F1.

benchmarks live in `benchmarks/results.md` including the runs that were
wrong. the failures are the useful part.
