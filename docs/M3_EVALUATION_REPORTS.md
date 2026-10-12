# M3 extraction evidence reports

The checkpoint can save its complete result instead of leaving it in a
terminal. It still uses `src.extract.evaluate`: exact start/end/type matches,
micro precision/recall/F1, and the same false positives and false negatives.
It does not change extraction, resolution weights, or the scheduled pipeline.

## Run it

From the repository root, with the normal requirements available:

```bash
python scripts/m3_checkpoint.py --skip-model --out reports/m3-golden.json
```

Without `--out`, the previous console tables and abbreviated disagreements
remain. The script also works by its absolute path from another directory;
no `PYTHONPATH` setup is needed. `--skip-model` avoids probing or loading the
optional ML dependencies. To compare the actual model, use the existing ML
environment and omit that flag; the first model load may download its weights.

The output is immutable using the existing M4 artifact writer. Repeating the
same run may reuse the same output path. If the content changes, choose a new
filename: the checkpoint refuses to replace a different or tampered report.
Output directories are created when needed. There are no added dependencies.

## What the JSON proves

Schema `1.0.0` includes:

- `dataset`: the repository-relative fixture path, SHA-256 of its exact file
  bytes, its own description, document/span counts, and explicit provenance.
- `documents`: every original text, its UTF-8 text hash, and every gold span.
  The `document_id` is `<dataset-sha256>:<zero-based-document-index>`. It is a
  stable fixture-row reference, **not** a publisher or production document ID.
- `extractors`: extractor name/version/language, configuration settings and
  their hash, overall and per-type TP/FP/FN and precision/recall/F1, all raw
  predictions, and **all** false-positive/false-negative spans. The console's
  display limit does not truncate the report.
- `scoring`: exact match, micro averaging, zero-denominator behavior, and the
  offset convention. Offsets are half-open Python Unicode code points into
  the unmodified original text. Every prediction and label must satisfy
  `text[start:end] == mention.text`, with valid nonempty bounds, before scoring.
- `code`, `configuration`, `inputs_sha256`, and `runtime`: fingerprints of the
  evaluation code, loaded gazetteer file and dataset, Git base revision and
  whether these inputs have uncommitted edits, plus Python/Unicode/relevant
  installed package versions. A source archive without Git reports null Git
  metadata; file hashes still identify its inputs. Changing inputs during a
  run prevents saving the artifact.
- `content_sha256`: the existing sealed-JSON digest, computed over canonical
  sorted-key JSON excluding the digest field itself. Verify it with
  `src.resolve.learning.content_sha256(report)`.

As in the existing evaluator, duplicate `(start, end, object_type)` keys count
once per document. Raw predictions remain visible, including duplicates;
error rows are deduplicated to agree with the TP/FP/FN counts. A wrong type
at the correct span is both a false positive for that predicted type and a
false negative for the gold type. Types absent from both predictions and
gold are omitted, rather than assigned invented scores.

Serialization has no wall-clock timestamp, elapsed time, absolute checkout
path, or output filename. The same inputs, environment metadata and outputs
produce the same report bytes. A new Git commit or changed runtime is a
different report even if its rounded metrics happen to match.

## What it does not prove

The checked-in `tests/golden/ner_eval.json` contains **15 authored sentences
and 35 labeled spans** at the time of this change. Its description says the
sentences are written in the style of the corpus. It has no publisher URLs,
source document identities, annotation author/date, independently reviewed
production sampling, or established holdout separation. These are useful
regression fixtures, not an estimate of live-news accuracy.

Every saved report therefore states `insufficient_real_world_evidence` and
`production_accuracy_supported: false`. It cannot remove that warning based
on a high score. The report does not substitute for the separate M4 scorer
gates in [M4_LABELING_AND_EVALUATION.md](M4_LABELING_AND_EVALUATION.md).

Production evaluation still needs a separately designed, independently
reviewed source sample and explicit-offset labels. The existing fixture
loader rejects repeated surface text because it cannot identify which
occurrence was labeled. This change does not broaden that loader or pretend
to establish production accuracy.

## Optional model states

- `skipped`: `--skip-model` was selected. Metrics are null.
- `unavailable`: ML dependencies are missing. Metrics are null.
- `initialization_error`: checking dependencies or loading the model failed. The baseline report is
  still saved, only the exception class is recorded, and the CLI exits 2.
- `inference_errors`: the wrapper logged an error during one or more document
  calls. The report records document indexes and exception classes, never
  tracebacks or private cache paths. Scores are diagnostic only; failed
  documents remain in the recall denominator. No comparison verdict is
  printed, and the CLI exits 2.
- `evaluated`: extraction ran with no observed inference errors. Empty
  predictions by themselves are valid outputs, not inferred failures.

Inference failures are observed with a temporary listener on the existing
`extract.model` logger, removed even on exceptions. This is not an independent
failure detector and does not change the production wrapper's behavior.
Invalid evidence fails before a report can be saved.

The wrapper version and settings are recorded. Resolved HuggingFace model
and tokenizer commits are included when exposed by the loaded objects, or
are explicitly null. This does **not** pin revisions or hash model weights;
deterministic serialization is not a claim that all future ML inference will
be bit-for-bit reproducible.

## Verification and Python notes

```bash
python -m pytest tests/unit/test_m3_checkpoint.py tests/unit/test_gazetteer.py \
  tests/unit/test_model_extractor.py tests/unit/test_automaton.py -q
python -m unittest discover -s tests/unit -p test_m3_checkpoint.py -v
```

The second command uses the standard-library test runner, avoiding the shared
database test fixture when only the lightweight extraction dependencies are
available. Fake models in these tests verify report plumbing and failures;
they are not model-quality measurements.

`Path.read_bytes()` hashes the actual file bytes, while `.encode("utf-8")`
makes the encoding of document strings explicit. Dictionary comprehensions
assemble named fingerprints without changing the underlying files.
`try/finally` removes the temporary logging listener whether extraction
succeeds or raises. `sort_keys=True` in the existing writer makes dictionary
insertion order irrelevant to saved bytes. None of these mechanisms changes
how true and false matches are decided.
