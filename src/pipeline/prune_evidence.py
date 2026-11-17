"""Prune the evidence rows of retracted entity generations (ADR 0031).

Entity resolution is a full recompute: every run retracts the previous
generation of entities and writes a new one, with one ``entity_mentions``
row and one ``provenance`` row per member mention. Under P6 the retracted
generations were kept forever, so the database grew by roughly one copy of
the corpus's mention links per run until it hit Neon's 1 GB project limit
and every scheduled run failed with ``DiskFull``.

This removes only the per-mention evidence of retracted entities. The
retracted entity rows themselves stay (so ids, ``supersedes_id`` chains and
retraction reasons remain on record), and the per-generation history that
answers "what did we believe at time X" lives in the stable-entity snapshot
and membership tables, which are untouched. Live entities, mentions,
documents, facts and every other provenance target are never touched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.store.orm import EntityMentionORM, EntityORM, ProvenanceORM

logger = logging.getLogger("pipeline.prune_evidence")

DEFAULT_BATCH_ENTITIES = 200
COMPACT_TABLES = ("entity_mentions", "provenance")


@dataclass(slots=True)
class PruneStats:
    retracted_entities: int = 0
    entity_mentions: int = 0
    provenance: int = 0
    applied: bool = False


def _retracted_entity_ids(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(EntityORM.id).where(EntityORM.retracted.is_(True)).order_by(EntityORM.id)
        )
    )


def _count(session: Session, ids: list[int]) -> tuple[int, int]:
    links = session.scalar(
        select(func.count()).select_from(EntityMentionORM).where(EntityMentionORM.entity_id.in_(ids))
    )
    prov = session.scalar(
        select(func.count())
        .select_from(ProvenanceORM)
        .where(ProvenanceORM.target_table == "entities", ProvenanceORM.target_id.in_(ids))
    )
    return int(links or 0), int(prov or 0)


def prune_retracted_entity_evidence(
    session: Session,
    *,
    apply: bool = False,
    batch_entities: int = DEFAULT_BATCH_ENTITIES,
    on_batch_done: Callable[[], None] | None = None,
) -> PruneStats:
    """Count (dry run) or delete the evidence rows of retracted entities.

    ``on_batch_done`` lets the caller commit after each batch so a large
    first-time cleanup never holds one huge transaction.
    """
    stats = PruneStats(applied=apply)
    ids = _retracted_entity_ids(session)
    stats.retracted_entities = len(ids)

    for start in range(0, len(ids), batch_entities):
        batch = ids[start : start + batch_entities]
        if not apply:
            links, prov = _count(session, batch)
        else:
            links = session.execute(
                delete(EntityMentionORM).where(EntityMentionORM.entity_id.in_(batch))
            ).rowcount
            prov = session.execute(
                delete(ProvenanceORM).where(
                    ProvenanceORM.target_table == "entities",
                    ProvenanceORM.target_id.in_(batch),
                )
            ).rowcount
            if on_batch_done is not None:
                on_batch_done()
        stats.entity_mentions += int(links or 0)
        stats.provenance += int(prov or 0)

    return stats


def compact_tables(engine: Engine, *, full: bool) -> None:
    """Reclaim space on Postgres after a prune.

    Plain VACUUM makes deleted space reusable by later inserts (enough for
    steady state). VACUUM FULL rewrites the tables and returns the space to
    the storage quota; it locks the tables, so only the one-off cleanup uses
    it. No-op on other backends (the SQLite test database).
    """
    if engine.dialect.name != "postgresql":
        return
    command = "VACUUM (FULL, ANALYZE)" if full else "VACUUM (ANALYZE)"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table in COMPACT_TABLES:
            logger.info("%s %s", command, table)
            conn.execute(text(f"{command} {table}"))


def run_prune_retracted_evidence(*, apply: bool = False, compact: str = "none") -> PruneStats:
    from src.store.database import get_core_engine, get_core_session

    with get_core_session() as session:
        stats = prune_retracted_entity_evidence(
            session, apply=apply, on_batch_done=session.commit if apply else None
        )
    if apply and compact != "none":
        compact_tables(get_core_engine(), full=compact == "full")
    return stats
