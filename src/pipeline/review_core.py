"""CLI-facing operations for M4's human review checkpoint."""

from __future__ import annotations

from pathlib import Path

from src.resolve.review import (
    decide_review_pair,
    export_review_labels,
    list_review_items,
    record_manual_merge,
    record_manual_split,
)
from src.store.database import get_core_session


def format_review_queue(status: str = "pending", limit: int = 20) -> str:
    with get_core_session() as session:
        items = list_review_items(session, status=status, limit=limit)
    if not items:
        return f"no {status} review pairs"

    lines = []
    for item in items:
        lines.append(
            f"[{item.id}] {item.status} score={item.score:.3f} "
            f"threshold={item.threshold:.3f} type={item.object_type}"
        )
        lines.append(
            f"    mention {item.left_mention_id}: {item.left_text}"
            f"  <->  mention {item.right_mention_id}: {item.right_text}"
        )
        feature_text = " ".join(f"{name}={value:.3f}" for name, value in item.features.items())
        lines.append(f"    {feature_text}")
    return "\n".join(lines)


def run_review_decision(
    review_pair_id: int,
    accept: bool,
    reviewer: str,
    reason: str | None = None,
) -> int:
    with get_core_session() as session:
        row = decide_review_pair(session, review_pair_id, accept, reviewer, reason)
        return row.id


def run_manual_merge(
    left_entity_id: int,
    right_entity_id: int,
    reviewer: str,
    reason: str | None = None,
) -> int:
    with get_core_session() as session:
        row = record_manual_merge(
            session, left_entity_id, right_entity_id, reviewer, reason
        )
        return row.id


def run_manual_split(
    entity_id: int,
    left_mention_id: int,
    right_mention_id: int,
    reviewer: str,
    reason: str | None = None,
) -> int:
    with get_core_session() as session:
        row = record_manual_split(
            session,
            entity_id,
            left_mention_id,
            right_mention_id,
            reviewer,
            reason,
        )
        return row.id


def run_export_review_labels(out_path: Path) -> int:
    with get_core_session() as session:
        payload = export_review_labels(session, out_path)
    return len(payload["pairs"])
