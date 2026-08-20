"""Apply one owner-authenticated entity-review GitHub issue.

GitHub Actions supplies the issue fields through environment variables.  The
title parser is deliberately strict: arbitrary issue text never becomes a
shell command or a database query fragment.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.resolve.review import decide_review_pair
from src.store.database import get_core_session
from src.store.orm import ResolutionDecisionORM

TITLE_PATTERN = re.compile(r"^\[entity-review\] pair ([1-9]\d*) (accept|reject)$")


@dataclass(frozen=True, slots=True)
class ReviewCommand:
    review_pair_id: int
    accept: bool


def parse_review_title(title: str) -> ReviewCommand:
    match = TITLE_PATTERN.fullmatch(title.strip())
    if match is None:
        raise ValueError("issue title is not a valid entity-review command")
    return ReviewCommand(
        review_pair_id=int(match.group(1)),
        accept=match.group(2) == "accept",
    )


def apply_review_issue(
    session: Session,
    title: str,
    actor: str,
    issue_number: int,
) -> tuple[int, bool]:
    """Record a decision once, returning ``(decision_id, created)``.

    GitHub permits workflow reruns.  The issue number is the idempotency key,
    so retrying a successful database write cannot append a duplicate answer.
    """
    command = parse_review_title(title)
    reason = f"GitHub issue #{issue_number}"
    existing = session.scalar(
        select(ResolutionDecisionORM).where(
            ResolutionDecisionORM.review_pair_id == command.review_pair_id,
            ResolutionDecisionORM.reviewer == actor,
            ResolutionDecisionORM.reason == reason,
        )
    )
    if existing is not None:
        return existing.id, False

    decision = decide_review_pair(
        session,
        command.review_pair_id,
        command.accept,
        reviewer=actor,
        reason=reason,
    )
    return decision.id, True


def main() -> None:
    title = os.environ["REVIEW_ISSUE_TITLE"]
    actor = os.environ["GITHUB_ACTOR"]
    repository_owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    issue_number = int(os.environ["REVIEW_ISSUE_NUMBER"])

    if actor != repository_owner:
        raise PermissionError("only the repository owner may record review decisions")

    with get_core_session() as session:
        decision_id, created = apply_review_issue(
            session, title, actor, issue_number
        )
    print(f"decision_id={decision_id} created={str(created).lower()}")


if __name__ == "__main__":
    main()
