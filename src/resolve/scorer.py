"""scores a pair as same thing or not, using LEARNED weights.

the brief is emphatic that the weights are learned and not hand tuned and
this is why. name similarity says بشار الاسد vs بشار الأسد is 0.96 and
حسن vs حسين is 0.933. one pair is the same person and one is not. those
numbers are almost the same so no threshold on that feature alone can
separate them. something has to weigh the other features against it and I
am not going to guess those weights better than fitting them will.

training happens offline in scripts/train_pair_scorer.py using sklearn and
writes the coefficients to config/pair_scorer_weights.json. inference here
is a dot product and a sigmoid in pure python. that split means the
scheduled pipeline never installs sklearn to score a pair, and it means the
weights are a reviewable artifact in the repo instead of a pickle nobody
can read.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from src.resolve.features import PairFeatures

WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "config" / "pair_scorer_weights.json"


@dataclass(frozen=True, slots=True)
class ScorerWeights:
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    threshold: float
    trained_at: str
    training_pairs: int
    auc: float

    @classmethod
    def load(cls, path: Path | None = None) -> "ScorerWeights":
        raw = json.loads((path or WEIGHTS_PATH).read_text(encoding="utf-8"))
        weights = cls(
            feature_names=tuple(raw["feature_names"]),
            coefficients=tuple(raw["coefficients"]),
            intercept=raw["intercept"],
            threshold=raw["threshold"],
            trained_at=raw["trained_at"],
            training_pairs=raw["training_pairs"],
            auc=raw["auc"],
        )
        # the saved feature order has to match what PairFeatures produces
        # today. if someone adds a feature and forgets to retrain, the
        # weights would line up with the wrong columns and the scorer would
        # be confidently wrong instead of loudly broken.
        expected = tuple(PairFeatures.names())
        if weights.feature_names != expected:
            raise ValueError(
                f"weights were trained on {weights.feature_names} but the code "
                f"now produces {expected}. retrain with scripts/train_pair_scorer.py"
            )
        return weights


def sigmoid(x: float) -> float:
    """squash a real number into a probability.

    the branch avoids math.exp overflowing on large negative inputs. exp(800)
    raises OverflowError and a scorer that crashes on a confident negative
    is worse than useless.
    """
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class PairScorer:
    """applies learned weights. no training code and no sklearn import."""

    def __init__(self, weights: ScorerWeights | None = None) -> None:
        self._weights = weights or ScorerWeights.load()

    @property
    def weights(self) -> ScorerWeights:
        return self._weights

    def probability(self, features: PairFeatures) -> float:
        """probability this pair is the same thing."""
        total = self._weights.intercept
        for coefficient, value in zip(self._weights.coefficients, features.as_vector()):
            total += coefficient * value
        return sigmoid(total)

    def is_match(self, features: PairFeatures) -> bool:
        return self.probability(features) >= self._weights.threshold

    def explain(self, features: PairFeatures) -> list[tuple[str, float, float]]:
        """per feature contribution to the score.

        this is what makes a merge defensible. when someone asks why the
        system thinks these two are the same thing I can point at which
        feature carried the decision rather than shrugging at a number.
        """
        return [
            (name, value, coefficient * value)
            for name, value, coefficient in zip(
                self._weights.feature_names, features.as_vector(), self._weights.coefficients
            )
        ]
