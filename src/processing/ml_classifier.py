"""TF-IDF + Logistic Regression topic classifier for Arabic articles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline


@dataclass(slots=True)
class EvaluationResult:
    accuracy: float
    macro_f1: float
    report: dict


class TfidfLogRegClassifier:
    """Production-friendly classical ML baseline.

    This class keeps a scikit-learn pipeline boundary that can later be swapped
    with transformer-based embeddings/classification (e.g., AraBERT) without
    changing upstream ingestion/processing contracts.
    """

    def __init__(self) -> None:
        self.pipeline = Pipeline(
            steps=[
                (
                    "tfidf",
                    TfidfVectorizer(
                        ngram_range=(1, 2),
                        min_df=2,
                        max_features=20000,
                        sublinear_tf=True,
                    ),
                ),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=1500,
                        class_weight="balanced",
                        n_jobs=None,
                    ),
                ),
            ]
        )

    def fit(self, texts: Iterable[str], labels: Iterable[str]) -> None:
        self.pipeline.fit(list(texts), list(labels))

    def predict(self, texts: Iterable[str]) -> list[str]:
        return list(self.pipeline.predict(list(texts)))

    def predict_proba(self, texts: Iterable[str]):
        return self.pipeline.predict_proba(list(texts))

    def evaluate(self, texts: Iterable[str], labels: Iterable[str]) -> EvaluationResult:
        y_true = list(labels)
        y_pred = self.predict(texts)
        return EvaluationResult(
            accuracy=accuracy_score(y_true, y_pred),
            macro_f1=f1_score(y_true, y_pred, average="macro"),
            report=classification_report(y_true, y_pred, output_dict=True),
        )

    def save(self, path: str) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, out_path)
        return out_path

    @classmethod
    def load(cls, path: str) -> "TfidfLogRegClassifier":
        instance = cls()
        instance.pipeline = joblib.load(path)
        return instance


def train_from_csv(
    csv_path: str,
    text_column: str = "text",
    label_column: str = "label",
    output_model_path: str = "data/processed/models/tfidf_logreg.joblib",
    test_size: float = 0.2,
    random_state: int = 42,
) -> EvaluationResult:
    """Train and evaluate model from labeled CSV data."""
    df = pd.read_csv(csv_path)
    if text_column not in df.columns or label_column not in df.columns:
        raise ValueError(
            f"CSV must contain '{text_column}' and '{label_column}' columns"
        )

    train_df, test_df = train_test_split(
        df,
        test_size=test_size,
        random_state=random_state,
        stratify=df[label_column],
    )

    classifier = TfidfLogRegClassifier()
    classifier.fit(train_df[text_column], train_df[label_column])
    evaluation = classifier.evaluate(test_df[text_column], test_df[label_column])
    classifier.save(output_model_path)

    return evaluation
