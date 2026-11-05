# Sample preparation notes

Status: downloaded and integrity checked; human review pending. No retrieval model has been run.
50 distinct documents from 19 sources: five existing practice documents plus 45 additional R2 downloads.
Database access consisted of schema inspection and SELECT queries only. Remote data was not modified.
Source gzips and decoded original UTF-8 texts are in originals/. Earlier practice files and decisions remain unchanged.

## Review workflow

Open REVIEW-40.md in VS Code. Fill in Your label and Your reason for each item; ten at a time is fine.
Use relevant, partly_relevant, irrelevant, or unsure.
Arabic verification and gloss corrections are separate from relevance.
The Markdown worksheet is the editable record of your next review.
review-40.json is the initial machine-readable draft; it will not automatically synchronize when you edit Markdown.
After review, import decisions into a new version while preserving this initial draft and the prior 12 reviews.
Do not run build-packet.cjs over review work: it intentionally refuses to overwrite outputs.

The 40 questions each currently have one proposed evidence passage. Additional candidate judgments will be needed for retrieval metrics.
Some questions deliberately ask for details absent from the displayed passage. Corpus-level unanswerability remains unreviewed.
No absence-based negative is eligible for training. Other valid answers must be checked first.
All 50 documents are quarantined together as evaluation candidates: no train/validation/test split is finalized.
This avoids putting related documents on opposing splits before story, duplicate, and relevant identity grouping is reviewed.
50 distinct hashes rule out byte-identical full documents; they do not rule out near duplicates or shared stories.
Questions are assistant-authored from the corpus, not an independent user-authored benchmark.
English glosses are assistant drafts. Neither gloss accuracy nor article factual accuracy has been independently verified.

## Sampling

The initial five are retained for continuity; they were convenience-selected.
The exact SELECT for the other 45 and returned metadata are saved in selected-metadata.json.
The query cycles through sources using a deterministic salted MD5 ordering, favors documents whose date and length terciles agree, and returns 45.
It covers all 19 sources but is not a probability sample of all articles, and the tercile preference creates a date/length association.
Topics were inspected after selection, not sampled from validated topic labels.
Do not infer whole-corpus performance from this sample.

## Data quality findings (assistant observations; preserve original facts)

- 10939 is a BBC app-migration notice, not a substantive news article. Retained as a quality-control document; no question uses it.
- 89 is a short video description, not a full transcript. A missing numeric answer here does not establish that the original video lacks it.
- 8790, 5425, 5676, and 2694 contain repeated text and/or unrelated headline material.
- 8563 and 13023 include app-promotion text.
- 2569 ends with a colon introducing further procedures; stored text may be incomplete.
- 4481, 12810, and 14477 contain bidirectional control characters. Originals were not normalized or stripped.
- 13407, 11952, and 14477 lack publication timestamps.
- 8790, 2694, 905, 5425, and 3423 have publication times after collection times. Flagged without correction; timezone or extraction causes are unverified.
- Several reports contain claims that require independent verification before being presented as real-world facts. This packet asks what the stored report supports.
- Public-source publication dates span years, rather than one current-news window.

## Data contract

manifest.json retains legacy numeric IDs, existing document_uid values mapped to document_id, original source URLs, publication and collection timestamps, original text hashes, R2 keys, and snapshot ID.
Neon document_uid uniqueness and foreign-key linkage were checked; long-term immutability was not independently proven.
Every review record repeats the provenance and has language/direction metadata.
Offsets use original Unicode code points, zero-based and end-exclusive. No text normalization occurred.
No retriever scores, abstention decisions, or model outputs exist yet; those fields are null rather than invented.

## Files

- manifest.json: local snapshot and provenance for all 50 documents.
- selected-metadata.json: exact selection query and 45 returned metadata records.
- originals/: 50 untouched gzip files and 50 decoded text files.
- packet-spec.json: authored questions, exact span anchors, draft English glosses.
- build-packet.cjs: local standard-library-only packet generator and integrity assertions.
- REVIEW-40.md: human-editable review worksheet.
- review-40.json: initial structured packet, all human labels blank.
- integrity-report.json: source/span checks, metadata flags, and initial artifact hashes.

No installations, model downloads, training, GPU jobs, repository edits, pushes, or deployment were performed.

