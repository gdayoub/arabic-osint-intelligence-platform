"""M3 checkpoint. scores every available extractor against the eval set.

the brief wants f1 for the gazetteer and the model side by side and says to
say so plainly if the model does not beat the baseline. so this prints both
columns and then states which one won without editorialising.

    python scripts/m3_checkpoint.py
    python scripts/m3_checkpoint.py --skip-model --out reports/m3-golden.json

the model column only appears when torch and transformers are installed.
they live in requirements-ml.txt and are deliberately not part of the
scheduled pipeline.

    pip install -r requirements.txt -r requirements-ml.txt

--out saves all evidence, not just the first few disagreements shown below.
The checked-in corpus is an authored regression fixture, not a production
accuracy sample. See docs/M3_EVALUATION_REPORTS.md for the report contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import platform
import subprocess
import sys
import unicodedata
from importlib import metadata
from pathlib import Path

# Match the other checkpoint CLIs: the documented direct invocation must
# work without an editable install or a PYTHONPATH environment variable.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract.base import ExtractedMention
from src.extract.evaluate import Score, disagreements, score_by_type, score_corpus
from src.extract.eval_set import EVAL_PATH, load_eval_set
from src.extract.gazetteer import GazetteerExtractor
from src.extract.model import DEFAULT_MODEL, ModelExtractor, is_available
from src.resolve.learning import content_sha256, write_immutable_json


SCHEMA_VERSION = "1.0.0"
CONFIG_PATHS = ("config/gazetteer.yaml",)
CODE_PATHS = (
    "scripts/m3_checkpoint.py",
    "src/extract/__init__.py",
    "src/extract/automaton.py",
    "src/extract/base.py",
    "src/extract/eval_set.py",
    "src/extract/evaluate.py",
    "src/extract/gazetteer.py",
    "src/extract/model.py",
    "src/lang/__init__.py",
    "src/lang/arabic.py",
    "src/lang/base.py",
    "src/lang/english.py",
    "src/lang/segmentation.py",
    "src/resolve/learning.py",  # existing sealed-JSON writer only
)
FIXTURE_WARNING = (
    "Authored golden regression fixture only. Insufficient real-world evidence: "
    "these scores do not establish production extraction accuracy."
)


class _ModelErrorCapture(logging.Handler):
    """Observe failures the existing model wrapper logs before returning [].

    A logging handler is a listener; it does not change inference or scoring.
    Keep only the exception type, never a traceback with local paths/secrets.
    """

    def __init__(self, errors: list[dict]) -> None:
        super().__init__(level=logging.ERROR)
        self.errors = errors
        self.document_index = 0

    def emit(self, record: logging.LogRecord) -> None:
        error_class = record.exc_info[0] if record.exc_info else None
        error_type = error_class.__name__ if error_class is not None else "LoggedError"
        self.errors.append({"document_index": self.document_index, "error_type": error_type})


def evaluate(extractor, documents, *, inference_errors: list[dict] | None = None):
    pairs = []
    logger = logging.getLogger("extract.model")
    capture = _ModelErrorCapture(inference_errors) if inference_errors is not None else None
    if capture is not None:
        logger.addHandler(capture)
    try:
        for index, document in enumerate(documents):
            if capture is not None:
                capture.document_index = index
            predicted = extractor.extract(document.text)
            gold = list(document.mentions)
            # Check P2 before scoring. A correctly indexed but wrong-type
            # prediction is an error; an invalid source span is a broken run.
            mention_rows(predicted, document.text)
            mention_rows(gold, document.text)
            pairs.append((predicted, gold))
    finally:
        if capture is not None:
            logger.removeHandler(capture)
    return pairs, score_corpus(pairs), score_by_type(pairs)


def score_record(score: Score) -> dict:
    return {
        "true_positives": score.true_positives,
        "false_positives": score.false_positives,
        "false_negatives": score.false_negatives,
        "precision": score.precision,
        "recall": score.recall,
        "f1": score.f1,
    }


def mention_rows(mentions: list[ExtractedMention], text: str, *, unique: bool = False) -> list[dict]:
    rows = []
    for mention in mentions:
        if (
            type(mention.start) is not int or type(mention.end) is not int
            or not 0 <= mention.start < mention.end <= len(text)
            or text[mention.start:mention.end] != mention.text
        ):
            raise ValueError("mention offsets do not match the original source text")
        if not isinstance(mention.object_type, str) or not mention.object_type:
            raise ValueError("mention object_type must be a non-empty string")
        if not math.isfinite(mention.confidence) or not 0 <= mention.confidence <= 1:
            raise ValueError("mention confidence must be finite and between zero and one")
        rows.append({
            "text": mention.text, "start": mention.start, "end": mention.end,
            "object_type": mention.object_type, "confidence": mention.confidence,
        })
    rows.sort(key=lambda row: (row["start"], row["end"], row["object_type"], row["confidence"]))
    if not unique:
        return rows
    # The existing evaluator counts distinct (start, end, type) keys. Error
    # rows must use that same identity or duplicate predictions inflate them.
    distinct = {}
    for row in rows:
        distinct.setdefault((row["start"], row["end"], row["object_type"]), row)
    return list(distinct.values())


def extractor_report(extractor, documents, evaluation, *, settings: dict, inference_errors=()) -> dict:
    pairs, overall, per_type = evaluation
    if len(pairs) != len(documents):
        raise ValueError("every evaluation document needs a prediction/gold pair")
    evidence = []
    for index, (document, (predicted, gold)) in enumerate(zip(documents, pairs)):
        if gold != list(document.mentions):
            raise ValueError("scored gold does not match the evaluation document")
        spurious, missed = disagreements(predicted, gold)
        evidence.append({
            "document_index": index,
            "predicted": mention_rows(predicted, document.text),
            "false_positives": mention_rows(spurious, document.text, unique=True),
            "false_negatives": mention_rows(missed, document.text, unique=True),
        })
    return {
        "name": extractor.name,
        "version": extractor.version,
        "language": extractor.language,
        "status": "inference_errors" if inference_errors else "evaluated",
        "settings": settings,
        "settings_sha256": content_sha256(settings),
        "overall": score_record(overall),
        "per_type": {key: score_record(per_type[key]) for key in sorted(per_type)},
        "evidence": evidence,
        "inference_errors": list(inference_errors),
        "observed_inference_error_count": len(inference_errors),
    }


def input_fingerprints() -> dict[str, str]:
    paths = (*CODE_PATHS, *CONFIG_PATHS, EVAL_PATH.relative_to(REPO_ROOT).as_posix())
    return {path: hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest() for path in paths}


def code_revision(paths) -> dict:
    """Git identifies the base; byte hashes also identify uncommitted edits."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        diff = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", *paths], cwd=REPO_ROOT,
            capture_output=True, check=False, timeout=5,
        )
        dirty = bool(diff.returncode) if diff.returncode in (0, 1) else None
        return {"git_commit": revision, "evaluation_inputs_dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "evaluation_inputs_dirty": None}


def runtime_versions() -> dict:
    versions = {"python": platform.python_version(), "unicode": unicodedata.unidata_version}
    for package in ("PyYAML", "torch", "transformers", "tokenizers", "huggingface-hub"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def build_report(documents, extractors: list[dict], fingerprints: dict[str, str]) -> dict:
    if input_fingerprints() != fingerprints:
        raise ValueError("evaluation input files changed during the run; rerun before saving")
    dataset_path = EVAL_PATH.relative_to(REPO_ROOT).as_posix()
    dataset_sha = fingerprints[dataset_path]
    document_rows = []
    for index, document in enumerate(documents):
        document_rows.append({
            "document_index": index,
            "document_id": f"{dataset_sha}:{index}",
            "text": document.text,
            "text_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
            "gold": mention_rows(list(document.mentions), document.text),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "mention_extraction_evaluation",
        "evidence_status": "insufficient_real_world_evidence",
        "production_accuracy_supported": False,
        "warning": FIXTURE_WARNING,
        "dataset": {
            "path": dataset_path, "sha256": dataset_sha,
            "provenance": "authored_golden_regression_fixture",
            "description": json.loads(EVAL_PATH.read_text(encoding="utf-8")).get("_comment"),
            "document_count": len(documents),
            "gold_span_count": sum(len(document.mentions) for document in documents),
            "source_document_ids_available": False,
            "source_urls_available": False,
            "annotation_author_and_date_available": False,
            "independent_holdout_established": False,
        },
        "scoring": {
            "match": "exact_start_end_and_object_type", "averaging": "micro",
            "duplicate_span_type_keys_counted_once": True,
            "zero_denominator_value": 0.0,
            "offset_unit": "python_unicode_code_points", "end_offset": "exclusive",
            "inference_error_metrics": "diagnostic_only; failed documents remain in the denominator",
        },
        "code": {**code_revision(fingerprints), "files_sha256": {
            path: fingerprints[path] for path in CODE_PATHS
        }},
        "configuration": {"files_sha256": {path: fingerprints[path] for path in CONFIG_PATHS}},
        "inputs_sha256": content_sha256(fingerprints),
        "runtime": runtime_versions(),
        "documents": document_rows,
        "extractors": extractors,
        "limitations": [
            "Fixture text is authored in the style of the corpus, not sampled source documents.",
            "Annotation review, temporal/source sampling and holdout separation are not established.",
            "Model revisions are observed when available, not pinned; file hashes do not hash model weights.",
            "Inference errors are observed from the wrapper's error logger, not an independent failure detector.",
            "This M3 report does not satisfy or replace the separate M4 scorer deployment gates.",
        ],
    }


def print_table(title: str, overall: Score, per_type: dict[str, Score]) -> None:
    print(f"\n{title}")
    print(f"{'type':<16}{'P':>8}{'R':>8}{'F1':>8}{'TP':>6}{'FP':>6}{'FN':>6}")
    print("-" * 58)
    for object_type in sorted(per_type):
        s = per_type[object_type]
        print(
            f"{object_type:<16}{s.precision:>8.2f}{s.recall:>8.2f}{s.f1:>8.2f}"
            f"{s.true_positives:>6}{s.false_positives:>6}{s.false_negatives:>6}"
        )
    print("-" * 58)
    print(
        f"{'OVERALL':<16}{overall.precision:>8.2f}{overall.recall:>8.2f}{overall.f1:>8.2f}"
        f"{overall.true_positives:>6}{overall.false_positives:>6}{overall.false_negatives:>6}"
    )


def print_disagreements(label, documents, pairs, limit: int = 6) -> None:
    print(f"\nwhat {label} got wrong")
    print("=" * 58)
    shown = 0
    for document, (predicted, gold) in zip(documents, pairs):
        spurious, missed = disagreements(predicted, gold)
        if not spurious and not missed:
            continue
        if shown >= limit:
            print("\n... more not shown")
            break
        shown += 1
        print(f"\n{document.text[:60]}")
        for m in spurious:
            print(f"   found but not gold : {m.text!r} as {m.object_type}")
        for m in missed:
            print(f"   missed             : {m.text!r} as {m.object_type}")


def model_revisions(model) -> dict:
    pipeline = getattr(model, "_pipeline", None)
    config = getattr(getattr(pipeline, "model", None), "config", None)
    tokenizer = getattr(pipeline, "tokenizer", None)
    candidates = {
        "model_resolved_commit": getattr(config, "_commit_hash", None),
        "tokenizer_resolved_commit": getattr(tokenizer, "init_kwargs", {}).get("_commit_hash"),
    }
    # A revision is a Hub commit, never an arbitrary cache path from a model
    # object. Unknown revisions stay explicitly null rather than guessed.
    for key, value in candidates.items():
        if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            candidates[key] = None
    return {"revision_pinned": False, **candidates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-model", action="store_true", help="gazetteer only even if torch is installed")
    parser.add_argument("--out", type=Path, help="save immutable JSON with full fixture evidence and fingerprints")
    args = parser.parse_args(argv)

    fingerprints = input_fingerprints()
    documents = load_eval_set()
    print(f"documents : {len(documents)}")
    print(f"gold spans: {sum(len(d.mentions) for d in documents)}")
    print(FIXTURE_WARNING)

    gazetteer = GazetteerExtractor(gazetteer_path=REPO_ROOT / "config/gazetteer.yaml")
    gaz_evaluation = evaluate(gazetteer, documents)
    gaz_pairs, gaz_overall, gaz_by_type = gaz_evaluation
    gaz_report = extractor_report(
        gazetteer, documents, gaz_evaluation,
        settings={"gazetteer_path": "config/gazetteer.yaml", "pattern_count": len(gazetteer)},
    )
    print_table(f"{gazetteer.name} v{gazetteer.version}  ({len(gazetteer)} patterns)", gaz_overall, gaz_by_type)

    model_report = {
        "name": ModelExtractor.name, "version": ModelExtractor.version,
        "language": ModelExtractor.language,
        "status": "skipped" if args.skip_model else "unavailable",
        "reason": "requested_skip" if args.skip_model else "missing_ml_dependencies",
        "overall": None, "per_type": None, "evidence": None,
    }
    exit_code = 0
    model_available = False
    if not args.skip_model:
        try:
            model_available = is_available()
        except Exception as exc:
            model_report.update({
                "status": "initialization_error", "reason": "model_availability_check_failed",
                "error_type": type(exc).__name__,
            })
            print(f"\nmodel dependency check failed ({type(exc).__name__}). no model scores recorded.")
            exit_code = 2
    if args.skip_model:
        print("\nmodel not evaluated (--skip-model).")
        print_disagreements("the gazetteer", documents, gaz_pairs)
    elif not model_available:
        if exit_code == 0:
            print("\nmodel not evaluated. install requirements-ml.txt to include it.")
        print_disagreements("the gazetteer", documents, gaz_pairs)
    else:
        print("\nloading the model. first run downloads a few hundred MB.")
        settings = {"model_name": DEFAULT_MODEL, "min_confidence": 0.5, "aggregation_strategy": "average"}
        try:
            model = ModelExtractor(**settings)
        except Exception as exc:
            model_report.update({
                "status": "initialization_error", "reason": "model_initialization_failed",
                "error_type": type(exc).__name__, "settings": settings,
                "settings_sha256": content_sha256(settings),
            })
            print(f"\nmodel initialization failed ({type(exc).__name__}). no model scores recorded.")
            print_disagreements("the gazetteer", documents, gaz_pairs)
            exit_code = 2
        else:
            errors = []
            model_evaluation = evaluate(model, documents, inference_errors=errors)
            model_pairs, model_overall, model_by_type = model_evaluation
            model_report = extractor_report(
                model, documents, model_evaluation, settings=settings, inference_errors=errors,
            )
            model_report["model_artifacts"] = model_revisions(model)
            print_table(f"{model.name} v{model.version}", model_overall, model_by_type)
            if errors:
                print(f"\n{len(errors)} observed model inference errors. metrics are diagnostic only; no verdict.")
                exit_code = 2
            else:
                print("\nverdict (on this golden fixture only)")
                print("=" * 58)
                delta = model_overall.f1 - gaz_overall.f1
                print(f"gazetteer F1 {gaz_overall.f1:.2f}   model F1 {model_overall.f1:.2f}   delta {delta:+.2f}")
                if delta > 0.01:
                    print("the model wins on this fixture. production benefit is still unproven.")
                elif delta < -0.01:
                    print("the model LOSES to a dictionary lookup. it does not earn its dependency yet.")
                else:
                    print("basically a tie. the model does not justify 2GB on this evidence.")

            print_disagreements("the gazetteer", documents, gaz_pairs, limit=3)
            print_disagreements("the model", documents, model_pairs, limit=3)

    if args.out:
        try:
            report = build_report(documents, [gaz_report, model_report], fingerprints)
            write_immutable_json(args.out, report)
        except (OSError, ValueError) as exc:
            print(f"\ncannot save evaluation report: {exc}", file=sys.stderr)
            return 2
        print(f"\nwrote evaluation report: {args.out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
