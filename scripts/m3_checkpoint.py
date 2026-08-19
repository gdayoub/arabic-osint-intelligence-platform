"""M3 checkpoint. scores every available extractor against the eval set.

the brief wants f1 for the gazetteer and the model side by side and says to
say so plainly if the model does not beat the baseline. so this prints both
columns and then states which one won without editorialising.

    python scripts/m3_checkpoint.py

the model column only appears when torch and transformers are installed.
they live in requirements-ml.txt and are deliberately not part of the
scheduled pipeline.

    pip install -r requirements.txt -r requirements-ml.txt
"""

from __future__ import annotations

import argparse

from src.extract.evaluate import Score, disagreements, score_by_type, score_corpus
from src.extract.eval_set import load_eval_set
from src.extract.gazetteer import GazetteerExtractor
from src.extract.model import ModelExtractor, is_available


def evaluate(extractor, documents):
    pairs = [(extractor.extract(d.text), list(d.mentions)) for d in documents]
    return pairs, score_corpus(pairs), score_by_type(pairs)


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-model", action="store_true", help="gazetteer only even if torch is installed")
    args = parser.parse_args()

    documents = load_eval_set()
    print(f"documents : {len(documents)}")
    print(f"gold spans: {sum(len(d.mentions) for d in documents)}")

    gazetteer = GazetteerExtractor()
    gaz_pairs, gaz_overall, gaz_by_type = evaluate(gazetteer, documents)
    print_table(f"{gazetteer.name} v{gazetteer.version}  ({len(gazetteer)} patterns)", gaz_overall, gaz_by_type)

    if args.skip_model or not is_available():
        print("\nmodel not evaluated. install requirements-ml.txt to include it.")
        print_disagreements("the gazetteer", documents, gaz_pairs)
        return

    print("\nloading the model. first run downloads a few hundred MB.")
    model = ModelExtractor()
    model_pairs, model_overall, model_by_type = evaluate(model, documents)
    print_table(f"{model.name} v{model.version}", model_overall, model_by_type)

    print("\nverdict")
    print("=" * 58)
    delta = model_overall.f1 - gaz_overall.f1
    print(f"gazetteer F1 {gaz_overall.f1:.2f}   model F1 {model_overall.f1:.2f}   delta {delta:+.2f}")
    if delta > 0.01:
        print("the model wins. it earns its dependency.")
    elif delta < -0.01:
        print("the model LOSES to a dictionary lookup. it does not earn its dependency yet.")
    else:
        print("basically a tie. the model does not justify 2GB on this evidence.")

    print_disagreements("the gazetteer", documents, gaz_pairs, limit=3)
    print_disagreements("the model", documents, model_pairs, limit=3)


if __name__ == "__main__":
    main()
