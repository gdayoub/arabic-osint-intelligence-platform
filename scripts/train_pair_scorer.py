"""fits the pair scorer weights and writes them to config.

run this offline. it needs sklearn which the scheduled pipeline does not
install. the output is a small json of coefficients that the pipeline reads
and applies with a dot product.

    pip install -r requirements-ml.txt
    python scripts/train_pair_scorer.py

why logistic regression and not something stronger. three reasons. it gives
me one coefficient per feature which I can read and argue with, it needs
almost no data to fit six parameters, and the output is a linear model I can
implement in ten lines of python so inference costs nothing. a gradient
boosted tree would probably score better and I would not be able to explain
any single decision it made.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneOut, cross_val_predict

from src.lang.arabic import ArabicAdapter
from src.resolve.features import MentionContext, PairFeatures, compute_features

REPO = Path(__file__).resolve().parents[1]
LABELS = REPO / "tests" / "golden" / "pair_labels.json"
OUT = REPO / "config" / "pair_scorer_weights.json"

ar = ArabicAdapter()

# co-occurring entities I attach to a pair depending on how much shared
# context the label says it has. this is a stand in. once M6 builds real
# co-occurrence links these come from the graph instead of from a hint.
# names I draw co-occurring context from. drawn at RANDOM and never from
# the label. see the warning in build_dataset for why that matters.
_CONTEXT_POOL = [
    "سوريا", "دمشق", "روسيا", "واشنطن", "الناتو", "بروكسل",
    "مصر", "القاهرة", "طهران", "بيروت", "غزة", "انقرة",
]


def _context(name: str, others: list[str], source: str, day_offset: int) -> MentionContext:
    return MentionContext(
        mention_id=0,
        normalized_name=ar.normalize(name),
        object_type="person",
        document_id=0,
        source=source,
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=day_offset),
        blocking_keys=frozenset(ar.blocking_keys(name)),
        co_mentions=frozenset(ar.normalize(o) for o in others),
    )


def build_dataset(review_labels_path: Path | None = None) -> tuple[list[list[float]], list[int]]:
    """turn the labelled name pairs into feature rows.

    READ THIS BEFORE CHANGING IT. my first version set source and date and
    context FROM THE LABEL. matching pairs got the same source one day
    apart, non matching pairs got different sources twenty days apart. that
    leaked the answer straight into the features and the model happily
    learned to ignore the name and read same_source instead. it scored AUC
    1.000 and the name_similarity weight came out at minus 0.013.

    an AUC of exactly 1.000 on a real task is not a triumph. it means the
    model found a shortcut, and the shortcut is almost always me handing it
    the label under a different name.

    so the context features are now drawn from a seeded RNG that never sees
    the label. they are honestly uninformative on this dataset and the model
    should learn a weight near zero for them, which is the correct thing to
    learn from data that carries no signal. name_similarity and key_overlap
    are the only features I actually have evidence for here and they are
    what the model has to use.

    when M6 builds real co-occurrence links these stop being random and get
    computed from the graph, and retraining will find real weights.
    """
    raw = json.loads(LABELS.read_text(encoding="utf-8"))
    rng = random.Random(20260819)
    sources = ["AlJazeeraArabic", "BBCArabic", "CNNArabic"]

    X: list[list[float]] = []
    y: list[int] = []

    for entry in raw["pairs"]:
        # every one of these is independent of entry["same"]
        others_a = rng.sample(_CONTEXT_POOL, k=3)
        others_b = rng.sample(_CONTEXT_POOL, k=3)
        source_a = rng.choice(sources)
        source_b = rng.choice(sources)
        day_a = rng.randint(0, 30)
        day_b = rng.randint(0, 30)

        a = _context(entry["a"], others_a, source_a, day_a)
        b = _context(entry["b"], others_b, source_b, day_b)

        X.append(compute_features(a, b).as_vector())
        y.append(1 if entry["same"] else 0)

    if review_labels_path is not None:
        reviewed = json.loads(review_labels_path.read_text(encoding="utf-8"))
        expected = PairFeatures.names()
        if reviewed.get("feature_names") != expected:
            raise ValueError(
                f"review labels contain {reviewed.get('feature_names')}, expected {expected}"
            )
        for entry in reviewed.get("pairs", []):
            vector = entry["feature_vector"]
            if len(vector) != len(expected):
                raise ValueError("review label feature vector has the wrong length")
            X.append([float(value) for value in vector])
            y.append(1 if entry["same"] else 0)

    return X, y


def main(review_labels_path: Path | None = None) -> None:
    X, y = build_dataset(review_labels_path)
    names = PairFeatures.names()

    model = LogisticRegression(max_iter=2000, C=1.0)
    model.fit(X, y)

    # leave one out because 51 pairs is far too few for a held out split.
    # training AUC on this little data would be optimistic to the point of
    # being a lie, so every prediction here comes from a model that never
    # saw that pair.
    predicted = cross_val_predict(
        LogisticRegression(max_iter=2000, C=1.0), X, y, cv=LeaveOneOut(), method="predict_proba"
    )[:, 1]
    auc = roc_auc_score(y, predicted)

    coefficients = model.coef_[0].tolist()

    print(f"trained on {len(y)} pairs. {sum(y)} positive {len(y)-sum(y)} negative")
    print(f"leave one out AUC {auc:.3f}")
    print()
    print("learned weights")
    for name, coefficient in sorted(zip(names, coefficients), key=lambda kv: abs(kv[1]), reverse=True):
        print(f"    {name:<22} {coefficient:+.3f}")
    print(f"    {'intercept':<22} {model.intercept_[0]:+.3f}")

    # precision recall at a few thresholds so the choice below is visible
    print()
    print("threshold   precision  recall")
    best_threshold, best_f1 = 0.5, 0.0
    for t in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
        tp = sum(1 for p, label in zip(predicted, y) if p >= t and label == 1)
        fp = sum(1 for p, label in zip(predicted, y) if p >= t and label == 0)
        fn = sum(1 for p, label in zip(predicted, y) if p < t and label == 1)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f1:
            best_threshold, best_f1 = t, f1
        print(f"    {t:.1f}       {precision:.2f}      {recall:.2f}")

    # I bias the threshold up from whatever maximises F1. a wrong merge is
    # much worse than a missed one because merging two people is very hard
    # to notice later and very hard to undo, while a missed merge just
    # leaves two entities that a human review queue can join.
    threshold = max(best_threshold, 0.6)

    OUT.write_text(
        json.dumps(
            {
                "_comment": (
                    "Learned by scripts/train_pair_scorer.py from tests/golden/pair_labels.json. "
                    "Do not hand edit. Retrain after changing PairFeatures or the label set."
                ),
                "feature_names": names,
                "coefficients": coefficients,
                "intercept": float(model.intercept_[0]),
                "threshold": threshold,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "training_pairs": len(y),
                "auc": float(auc),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print()
    print(f"threshold {threshold} chosen. biased toward precision on purpose")
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit the entity pair scorer")
    parser.add_argument(
        "--review-labels",
        type=Path,
        help="JSON produced by `python main.py review export-labels`",
    )
    args = parser.parse_args()
    main(args.review_labels)
