# Resume bilingual retrieval

Checkpoint: 2026-11-05. Branch: `experiment/bilingual-retrieval`.

## Start here, next agent

George is learning, not asking you to finish the model for him. Read repository
`AGENTS.md`, `docs/AGENT_BRIEF.md`, and this directory's `AGENTS.md`. Explain one small
step, give hints, wait for his attempt, then review. Avoid a wall of terminal commands.
His current `first_retrieval.py` contains only `import json`; preserve his work.
The next exercise is to read `data/labels/review-40.json`, select `records[0]`, and
print `query_en` and `passage.original_span`. Resolve paths from the script location.
Do not implement that exercise on his behalf.

## What is included

- 50 local Arabic documents from 19 sources, original UTF-8 and gzip copies, plus metadata.
- 40 assistant-authored English questions, Arabic evidence spans, and English glosses.
- Saved human review history: `data/labels/button-review-history.jsonl` is authoritative.
  Fold entries in order by question ID to obtain the latest decisions. The packet's
  original null labels are not the final reviews. Latest snapshot has 40 approvals:
  33 relevant, 4 partly relevant, 3 irrelevant; 39 Arabic verification checkboxes checked.
  Checkboxes are self-reports, not independent validation.
- `adjudication-v1.json`: six assistant flags awaiting discussion, not label corrections.
- Q14 manual multi-passage demo and its append-only review history. This was not E5 output.
- 220 overlapping original-text passages, 40 prepared queries, and raw English/Arabic
  BM25 rankings. This lexical baseline has a strong cross-language mismatch.
- `archive/`: older practice, synthetic UI tests, historical paths, and prior derived
  snapshots. Do not merge its test labels or old packets into current judgments.

E5 has NOT run. No embedding cache, model checkpoint, training set, trained model,
frozen test set, or validated Recall/nDCG/MRR result exists. The sample was curated,
not a random representative draw. Article count is not label count.

## Start on the other computer

If you do not already have a checkout, clone this branch into your preferred project folder:

```text
git clone --branch experiment/bilingual-retrieval https://github.com/gdayoub/arabic-osint-intelligence-platform.git bilingual-retrieval
```

If a checkout already exists, inspect its changes and worktrees before fetching or
switching branches; do not reset or overwrite another task's work. Open the repository
folder in VS Code. No old laptop paths or bundled Codex runtime are required.

Check available Python and Node before suggesting commands. The helpers use only
standard libraries; Python 3.11+ and a current supported Node are suitable. No pip or
npm install is needed for the current JSON exercise and review UI. From repository root,
with the verified executable names for that machine:

```text
python retrieval-exercise/scripts/verify-snapshot.py
node retrieval-exercise/review/review-server.cjs
```

The first command checks source hashes, spans, journals, and ranked passage IDs without
changing data. The second opens a local HTTP listener on 127.0.0.1:8874. Visit that URL;
Ctrl+C in its terminal stops it. It writes new decisions to the tracked review journal.
Do not start another server on the same port. There is no build step or cloud access.

The preparation and lexical scripts are rerunnable but overwrite their generated files:
`scripts/prepare-corpus.py` writes `data/prepared/`; `scripts/lexical-baseline.py` writes
`results/lexical-rankings.json`. Existing source and label files are not overwritten.
`scripts/build-packet.cjs` refuses to overwrite the original packet; do not remove that guard.

## Next model milestone, after the JSON lesson

Evaluate unchanged `intfloat/multilingual-e5-small` as a shared dual encoder: encode
English queries and Arabic passages independently, then exact CPU search over normalized
vectors. Previously checked candidate revision:
`614241f622f53c4eeff9890bdc4f31cfecc418b3` (MIT).
Recheck primary documentation before installing: https://huggingface.co/intfloat/multilingual-e5-small
and https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html .
Expected setup is literal `query: ` / `passage: ` prefixes even for Arabic, masked mean
pooling, L2 normalization, 384 dimensions, and a 512-token limit. Verify these against
the pinned model and never silently truncate evidence spans.

Earlier proposals mentioned CPU torch 2.9.1 and transformers 4.57.1, but neither was
installed here and George did not approve that download. First inspect the new computer,
justify dependencies, and agree on a separate environment and download. The laptop's
448 environment had torch 2.11.0+cu128 verified; preserve it. Its RTX 5070 Laptop had
8 GB VRAM; do not assume the other computer matches. Coordinate GPU/artifacts with
other model work; never launch four GPU jobs together.

## Data and evaluation rules

No database or R2 access is needed to resume. Reported total database size was about
14,000 articles, not independently confirmed current coverage. This snapshot contains 50.
Metadata came from Neon and original article blobs from R2 in the previously agreed
export. Do not request credentials or expand data access without a scoped agreement.

Preserve document IDs, source references, text hashes, timestamps, snapshot IDs, and
Unicode code-point spans. Existing UID uniqueness is known; long-term immutability is
not independently established. Missing timestamps and source-quality flags remain in
the manifest. Do not normalize stored text. English glosses may need correction.

Pool multiple candidates per query for human review, including other potential valid
answers. Label missing facts as passage-specific; do not infer corpus unanswerability.
Split story, duplicate, and relevant identity groups before making training pairs.
Freeze a human-reviewed test set; pseudo-labels belong only in training. Avoid false
negatives. Evaluate lexical and unchanged E5 baselines with Recall@k, nDCG, and MRR
only once suitable judgments exist, and inspect misses and citation validity.

Train only if labels and baseline errors justify it. If pretrained retrieval suffices,
freeze it and consider event matching as the first custom-trained model. Retrieval
does not decide whether articles describe the same event. A compact cross-encoder is
a later separate ablation. Store derived model outputs in versioned sidecars, including
configuration, dataset hashes, scores, and abstention. Cache embeddings by exact model
configuration and text hash; process changed text incrementally.

Create a private root `.project-guide.md` locally, keep it ignored, and set Hidden on
Windows. It intentionally does not travel through Git. Do not publish credentials,
environments, caches, or model weights. No PR, merge, or deployment is part of this handoff.
