"""Owner-authenticated GitHub issue bridge for dashboard review decisions."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from scripts.apply_review_issue import apply_review_issue, parse_review_title
from src.pipeline.resolve_core import resolve_all
from src.store.orm import ResolutionDecisionORM, ReviewPairORM
from tests.unit.test_review import FixedScorer, _make_mentions


def test_parse_review_title_accepts_only_exact_commands():
    accepted = parse_review_title("[entity-review] pair 42 accept")
    rejected = parse_review_title("[entity-review] pair 7 reject")

    assert (accepted.review_pair_id, accepted.accept) == (42, True)
    assert (rejected.review_pair_id, rejected.accept) == (7, False)
    for invalid in (
        "entity-review pair 42 accept",
        "[entity-review] pair 0 accept",
        "[entity-review] pair 42 merge",
        "[entity-review] pair 42 accept; rm -rf /",
    ):
        with pytest.raises(ValueError):
            parse_review_title(invalid)


def test_apply_review_issue_is_idempotent(session, ontology, blob_store):
    _make_mentions(session, blob_store, ontology, ["جورج دايوب", "جورج داوب"])
    resolve_all(session, ontology, scorer=FixedScorer(0.55), review_margin=0.10)
    session.flush()
    pair = session.scalar(select(ReviewPairORM))
    title = f"[entity-review] pair {pair.id} accept"

    first_id, first_created = apply_review_issue(session, title, "gdayoub", 123)
    session.flush()
    second_id, second_created = apply_review_issue(session, title, "gdayoub", 123)

    assert (first_id, first_created) == (second_id, True)
    assert second_created is False
    assert len(session.scalars(select(ResolutionDecisionORM)).all()) == 1
