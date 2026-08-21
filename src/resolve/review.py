"""Human review queue and append-only resolution decisions.

The scorer proposes pairs; a person supplies the label.  Queue rows preserve
what the model saw at that moment, while decision rows preserve who answered
and whether a later answer superseded it.  Both rows point back to the two
source mentions through provenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import ExtractorVersion
from src.resolve.features import MentionContext, PairFeatures
from src.store.identity import record_resolution_constraint
from src.store.orm import (
    EntityMentionORM,
    EntityORM,
    MentionORM,
    ProvenanceORM,
    ResolutionDecisionORM,
    ReviewPairORM,
)
from src.store.provenance import register_extractor_version

Decision = Literal["same", "different"]

HUMAN_EXTRACTOR_NAME = "human_resolution_review"
HUMAN_EXTRACTOR_VERSION = "1.0.0"


def ordered_pair(a: int, b: int) -> tuple[int, int]:
    if a == b:
        raise ValueError("a resolution decision needs two different mentions")
    return (a, b) if a < b else (b, a)


@dataclass(frozen=True, slots=True)
class ReviewItem:
    id: int
    left_mention_id: int
    right_mention_id: int
    left_text: str
    right_text: str
    object_type: str
    score: float
    threshold: float
    features: dict[str, float]
    status: str


def enqueue_review_pair(
    session: Session,
    left: MentionContext,
    right: MentionContext,
    score: float,
    threshold: float,
    features: PairFeatures,
    scorer_version: ExtractorVersion,
) -> bool:
    """Insert one immutable queue snapshot, returning True when it was new."""
    left_id, right_id = ordered_pair(left.mention_id, right.mention_id)
    existing = session.scalar(
        select(ReviewPairORM).where(
            ReviewPairORM.left_mention_id == left_id,
            ReviewPairORM.right_mention_id == right_id,
            ReviewPairORM.scorer_version_id == scorer_version.id,
        )
    )
    if existing is not None:
        return False

    by_id = {left.mention_id: left, right.mention_id: right}
    row = ReviewPairORM(
        left_mention_id=left_id,
        right_mention_id=right_id,
        object_type=left.object_type,
        score=score,
        threshold=threshold,
        features=dict(zip(PairFeatures.names(), features.as_vector())),
        scorer_version_id=scorer_version.id,
    )
    session.add(row)
    session.flush()

    for mention_id in (left_id, right_id):
        context = by_id[mention_id]
        session.add(
            ProvenanceORM(
                target_table="review_pairs",
                target_id=row.id,
                document_id=context.document_id,
                mention_id=mention_id,
                extractor_version_id=scorer_version.id,
            )
        )
    return True


def latest_decisions(session: Session) -> dict[tuple[int, int], ResolutionDecisionORM]:
    """Return the newest human answer for each mention pair.

    Decisions are ordered by id rather than wall-clock time.  The id is the
    database's unambiguous append order and avoids equal timestamp edge cases.
    """
    rows = session.scalars(select(ResolutionDecisionORM).order_by(ResolutionDecisionORM.id)).all()
    latest: dict[tuple[int, int], ResolutionDecisionORM] = {}
    for row in rows:
        latest[(row.left_mention_id, row.right_mention_id)] = row
    return latest


def record_decision(
    session: Session,
    left_mention_id: int,
    right_mention_id: int,
    decision: Decision,
    reviewer: str,
    source: str,
    reason: str | None = None,
    review_pair_id: int | None = None,
) -> ResolutionDecisionORM:
    """Append a human decision and two provenance rows in one transaction."""
    if decision not in ("same", "different"):
        raise ValueError("decision must be 'same' or 'different'")
    if not reviewer.strip():
        raise ValueError("reviewer must not be empty")

    left_id, right_id = ordered_pair(left_mention_id, right_mention_id)
    left = session.get(MentionORM, left_id)
    right = session.get(MentionORM, right_id)
    if left is None or right is None:
        raise ValueError("both mention ids must exist")
    if left.object_type != right.object_type:
        raise ValueError("cannot resolve mentions with different object types")

    review_pair = session.get(ReviewPairORM, review_pair_id) if review_pair_id else None
    if review_pair_id is not None and review_pair is None:
        raise ValueError(f"review pair {review_pair_id} does not exist")
    if review_pair is not None and (
        review_pair.left_mention_id != left_id or review_pair.right_mention_id != right_id
    ):
        raise ValueError("review pair does not contain the supplied mentions")

    prior = latest_decisions(session).get((left_id, right_id))
    extractor = register_extractor_version(
        session,
        HUMAN_EXTRACTOR_NAME,
        HUMAN_EXTRACTOR_VERSION,
        description="Human entity-resolution decision",
    )
    row = ResolutionDecisionORM(
        review_pair_id=review_pair_id,
        left_mention_id=left_id,
        right_mention_id=right_id,
        decision=decision,
        source=source,
        reviewer=reviewer.strip(),
        reason=reason,
        extractor_version_id=extractor.id,
        supersedes_id=prior.id if prior else None,
    )
    session.add(row)
    session.flush()

    for mention in (left, right):
        session.add(
            ProvenanceORM(
                target_table="resolution_decisions",
                target_id=row.id,
                document_id=mention.document_id,
                mention_id=mention.id,
                extractor_version_id=extractor.id,
            )
        )
    # Keep the existing raw decision semantics intact while writing its
    # durable-evidence counterpart in this same transaction.  A legacy row
    # without mappings is intentionally refused rather than guessed; the
    # explicit M4.2a adoption command must establish those mappings first.
    record_resolution_constraint(session, row)
    return row


def decide_review_pair(
    session: Session,
    review_pair_id: int,
    accept: bool,
    reviewer: str,
    reason: str | None = None,
) -> ResolutionDecisionORM:
    pair = session.get(ReviewPairORM, review_pair_id)
    if pair is None:
        raise ValueError(f"review pair {review_pair_id} does not exist")
    return record_decision(
        session,
        pair.left_mention_id,
        pair.right_mention_id,
        "same" if accept else "different",
        reviewer,
        source="review_queue",
        reason=reason,
        review_pair_id=pair.id,
    )


def record_manual_merge(
    session: Session,
    left_entity_id: int,
    right_entity_id: int,
    reviewer: str,
    reason: str | None = None,
) -> ResolutionDecisionORM:
    """Persist a must-link constraint anchored by one mention from each entity."""
    if left_entity_id == right_entity_id:
        raise ValueError("manual merge needs two different entities")
    entities = [session.get(EntityORM, entity_id) for entity_id in (left_entity_id, right_entity_id)]
    if any(entity is None or entity.retracted for entity in entities):
        raise ValueError("manual merge requires two live entities")
    if entities[0].object_type != entities[1].object_type:
        raise ValueError("cannot merge entities with different object types")

    anchors: list[int] = []
    for entity_id in (left_entity_id, right_entity_id):
        mention_id = session.scalar(
            select(EntityMentionORM.mention_id)
            .where(EntityMentionORM.entity_id == entity_id)
            .order_by(EntityMentionORM.mention_id)
            .limit(1)
        )
        if mention_id is None:
            raise ValueError(f"entity {entity_id} has no evidence mentions")
        anchors.append(mention_id)

    return record_decision(
        session,
        anchors[0],
        anchors[1],
        "same",
        reviewer,
        source="manual_merge",
        reason=reason,
    )


def record_manual_split(
    session: Session,
    entity_id: int,
    left_mention_id: int,
    right_mention_id: int,
    reviewer: str,
    reason: str | None = None,
) -> ResolutionDecisionORM:
    """Persist a cannot-link constraint between two mentions in one entity."""
    entity = session.get(EntityORM, entity_id)
    if entity is None or entity.retracted:
        raise ValueError("manual split requires a live entity")
    member_ids = set(
        session.scalars(
            select(EntityMentionORM.mention_id).where(EntityMentionORM.entity_id == entity_id)
        ).all()
    )
    if left_mention_id not in member_ids or right_mention_id not in member_ids:
        raise ValueError("both mentions must belong to the entity being split")
    return record_decision(
        session,
        left_mention_id,
        right_mention_id,
        "different",
        reviewer,
        source="manual_split",
        reason=reason,
    )


def list_review_items(
    session: Session, status: str = "pending", limit: int = 20
) -> list[ReviewItem]:
    if status not in ("pending", "accepted", "rejected", "all"):
        raise ValueError("status must be pending, accepted, rejected, or all")

    decisions = latest_decisions(session)
    rows = session.scalars(select(ReviewPairORM).order_by(ReviewPairORM.created_at, ReviewPairORM.id)).all()
    mention_ids = {mention_id for row in rows for mention_id in (row.left_mention_id, row.right_mention_id)}
    mentions = {
        row.id: row
        for row in session.scalars(select(MentionORM).where(MentionORM.id.in_(mention_ids))).all()
    }

    items: list[ReviewItem] = []
    for row in rows:
        decision = decisions.get((row.left_mention_id, row.right_mention_id))
        row_status = "pending"
        if decision is not None:
            row_status = "accepted" if decision.decision == "same" else "rejected"
        if status != "all" and row_status != status:
            continue
        items.append(
            ReviewItem(
                id=row.id,
                left_mention_id=row.left_mention_id,
                right_mention_id=row.right_mention_id,
                left_text=mentions[row.left_mention_id].text,
                right_text=mentions[row.right_mention_id].text,
                object_type=row.object_type,
                score=row.score,
                threshold=row.threshold,
                features=row.features,
                status=row_status,
            )
        )
        if len(items) >= limit:
            break
    return items


def export_review_labels(session: Session, out_path: Path) -> dict:
    """Export latest queue decisions with their real inference features."""
    decisions = latest_decisions(session)
    review_rows = session.scalars(select(ReviewPairORM).order_by(ReviewPairORM.id)).all()
    # One training example per mention pair.  If a newer scorer queued the
    # same pair again, use that newest feature snapshot with the latest human
    # answer rather than double-counting the label.
    latest_review_by_pair: dict[tuple[int, int], ReviewPairORM] = {}
    for row in review_rows:
        latest_review_by_pair[(row.left_mention_id, row.right_mention_id)] = row
    mention_ids = {
        mention_id
        for row in latest_review_by_pair.values()
        for mention_id in (row.left_mention_id, row.right_mention_id)
    }
    mentions = {
        row.id: row
        for row in session.scalars(select(MentionORM).where(MentionORM.id.in_(mention_ids))).all()
    }

    pairs = []
    for pair_key, decision in decisions.items():
        review = latest_review_by_pair.get(pair_key)
        if review is None:
            continue
        pairs.append(
            {
                "a": mentions[pair_key[0]].text,
                "b": mentions[pair_key[1]].text,
                "same": decision.decision == "same",
                "object_type": review.object_type,
                "feature_vector": [review.features[name] for name in PairFeatures.names()],
                "review_pair_id": review.id,
                "decision_id": decision.id,
            }
        )

    payload = {
        "_comment": "Human labels from real blocking-surviving production pairs.",
        "feature_names": PairFeatures.names(),
        "pairs": pairs,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
