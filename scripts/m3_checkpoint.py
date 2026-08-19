"""M3 checkpoint. scores the gazetteer against the hand labelled eval set.

the brief wants f1 for both extractors side by side and says to say so
plainly if the model does not beat the baseline. right now only the
gazetteer exists so this prints the baseline column and nothing else. the
model column shows up when the transformer does.

    python scripts/m3_checkpoint.py
"""

from __future__ import annotations

from src.extract.evaluate import disagreements, score_by_type, score_corpus
from src.extract.eval_set import load_eval_set
from src.extract.gazetteer import GazetteerExtractor


def main() -> None:
    extractor = GazetteerExtractor()
    documents = load_eval_set()

    pairs = [(extractor.extract(d.text), list(d.mentions)) for d in documents]

    overall = score_corpus(pairs)
    per_type = score_by_type(pairs)

    print(f"extractor : {extractor.name} v{extractor.version}")
    print(f"patterns  : {len(extractor)}")
    print(f"documents : {len(documents)}")
    print(f"gold spans: {sum(len(d.mentions) for d in documents)}")
    print()

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

    print()
    print("what it got wrong")
    print("=" * 58)
    for document, (predicted, gold) in zip(documents, pairs):
        spurious, missed = disagreements(predicted, gold)
        if not spurious and not missed:
            continue
        print(f"\n{document.text[:60]}")
        for m in spurious:
            print(f"   found but not gold : {m.text!r} as {m.object_type}")
        for m in missed:
            print(f"   missed             : {m.text!r} as {m.object_type}")


if __name__ == "__main__":
    main()
