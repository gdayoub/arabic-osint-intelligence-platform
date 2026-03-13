"""Generate starter labeled data for ML classifier bootstrapping.

This is intentionally small and synthetic; replace with analyst-labeled data
for serious model performance.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SEED_ROWS = [
    {"text": "اندلاع اشتباك بين قوات الجيش وقصف صاروخي", "label": "Military"},
    {"text": "الحكومة تعلن نتائج الانتخابات البرلمانية", "label": "Politics"},
    {"text": "خروج مظاهرة كبيرة احتجاجا على القرارات", "label": "Protests"},
    {"text": "ارتفاع التضخم وتراجع قيمة العملة", "label": "Economy"},
    {"text": "وصول مساعدات إنسانية للنازحين", "label": "Humanitarian"},
]


def export_seed_labels(output_csv: str = "data/processed/seed_training_data.csv") -> Path:
    """Write a tiny starter dataset used to validate ML training pipeline."""
    df = pd.DataFrame(SEED_ROWS)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path


if __name__ == "__main__":
    path = export_seed_labels()
    print(f"Seed labels exported: {path}")
