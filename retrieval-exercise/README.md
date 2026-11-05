# Bilingual retrieval

Start with `first_retrieval.py`. This is George's learning file; the helper scripts
were prepared by the assistant and are separate from the code George is writing.

## Folder guide

| Folder | What belongs here |
| --- | --- |
| `data/articles/` | The 50 original articles, their manifest, and source metadata |
| `data/labels/` | The 40-question packet, saved human decisions, and review notes |
| `data/prepared/` | Generated passage chunks and query inputs for retrieval |
| `scripts/` | Existing data preparation and lexical baseline helpers |
| `review/` | The local button review interface and server |
| `results/` | Generated retrieval rankings |
| `archive/` | Earlier practice, historical worksheets, and previous outputs |

## The JSON for your current lesson

Open **data/labels/review-40.json**. Its `records` list contains each English query,
original Arabic passage, and English explanation. It is the original question packet;
George's saved decisions are separately recorded in **data/labels/button-review-history.jsonl**.
Do not read the initial packet's blank human labels as George's final decisions.

In `first_retrieval.py`, build the packet path from the file's containing folder:

```python
folder / "data" / "labels" / "review-40.json"
```

The manifest is **data/articles/manifest.json**. Raw text is in
**data/articles/originals/**. Existing `local_text_file` values in the manifest and
review records are relative to `data/articles/`, not the label file's directory.
Prepared corpus paths are relative to `data/prepared/`. The review server resolves
these locations explicitly without modifying the original packet or its hashes.

## Workspace and Git

Root: `C:\Users\george\Desktop\Projects\bilingual-retrieval`

Branch: `experiment/bilingual-retrieval`, a worktree of the existing backend repository.
The code, 50-article sample, labels, prepared passages, results, and historical archive
are now included in this branch for transfer to another computer. Model downloads,
environments, logs, and the owner's private `.project-guide.md` remain excluded.
See **HANDOFF.md** for the exact resume point and setup instructions. New reviews
are saved locally until you deliberately commit and push them.

The reorganization record in `archive/reorganization-2026-10-17.json` records old
paths, new paths, and hashes before helper-code path updates. Source text and human
judgments were moved byte-for-byte. Older derived outputs remain archived; current
prepared files and lexical rankings were regenerated with the new paths.

## Learning checkpoint

E5 has not been installed or run here. No training has happened. Agree on dependencies
and the model download before setting up a separate environment; preserve the 448
course environment. George writes and understands the model code one small step at a time.

The next exercise is to read one record from `data/labels/review-40.json`.

The review server lives at `review/review-server.cjs` and serves
http://127.0.0.1:8874/ while running. Refresh an old tab after a restart before saving.
The server uses only Node's standard library. Archived worksheets may still contain
historical paths; use the current data files and review page for ongoing work.
