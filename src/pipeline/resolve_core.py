"""resolves mentions into entities. M4 wired into the pipeline.

the flow. load mentions with enough context to score them, block to get
candidate pairs, score each pair with the learned weights, cluster whatever
clears the threshold, then write one entity per cluster with every member
mention attached as evidence.

this is a full recompute and not an incremental update. resolution is global
by nature: a new mention can merge two clusters that were separate
yesterday, so there is no correct way to resolve one mention on its own. at
the current corpus size a recompute takes seconds. if it stops taking
seconds the fix is incremental blocking and not a smarter loop, and P7 says
measure before building that.

previous entities get retracted rather than deleted (P6) so the history of
what the system believed stays answerable.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.models import Mention
from src.core.ontology import Ontology
from src.lang.arabic import ArabicAdapter
from src.resolve.blocking import KeyBlocker
from src.resolve.cluster import cluster_pairs
from src.resolve.features import MentionContext, compute_features
from src.resolve.scorer import PairScorer
from src.store.database import get_core_session
from src.store.orm import DocumentORM, EntityORM, MentionORM
from src.store.provenance import add_entity_evidence, create_entity, register_extractor_version

logger = logging.getLogger("pipeline.resolve_core")

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "config" / "ontology.yaml"

EXTRACTOR_NAME = "pair_scorer_resolver"
EXTRACTOR_VERSION = "1.0.0"


@dataclass(slots=True)
class ResolveStats:
    mentions: int = 0
    candidate_pairs: int = 0
    full_pairs: int = 0
    matched_pairs: int = 0
    entities_created: int = 0
    entities_retracted: int = 0
    giant_components_split: int = 0
    exact_duplicate_groups: int = 0
    size_histogram: dict[int, int] = field(default_factory=dict)

    @property
    def reduction_ratio(self) -> float:
        if not self.full_pairs:
            return 0.0
        return 1.0 - (self.candidate_pairs / self.full_pairs)


def load_mention_contexts(session: Session, adapter: ArabicAdapter) -> dict[int, MentionContext]:
    """pull every live mention with the context the scorer needs.

    one query with a join instead of a query per mention. co_mentions gets
    built by grouping in python because the alternative is a self join
    returning every pair of mentions sharing a document, which is the same
    quadratic blowup blocking exists to avoid.
    """
    rows = session.execute(
        select(
            MentionORM.id,
            MentionORM.text,
            MentionORM.object_type,
            MentionORM.document_id,
            DocumentORM.source,
            DocumentORM.published_at,
        )
        .join(DocumentORM, DocumentORM.id == MentionORM.document_id)
        .where(MentionORM.retracted.is_(False), DocumentORM.retracted.is_(False))
    ).all()

    by_document: dict[int, list[str]] = defaultdict(list)
    for _mid, text, _type, document_id, _source, _published in rows:
        by_document[document_id].append(adapter.normalize(text))

    contexts: dict[int, MentionContext] = {}
    for mention_id, text, object_type, document_id, source, published_at in rows:
        normalized = adapter.normalize(text)
        contexts[mention_id] = MentionContext(
            mention_id=mention_id,
            normalized_name=normalized,
            object_type=object_type,
            document_id=document_id,
            source=source,
            published_at=published_at,
            blocking_keys=frozenset(adapter.blocking_keys(text)),
            # everything else named in the same article. I drop the mention's
            # own name so it does not count itself as its own context.
            co_mentions=frozenset(n for n in by_document[document_id] if n != normalized),
        )
    return contexts


def pick_canonical_name(members: list[int], raw_text: dict[int, str]) -> str:
    """decide what to call the merged entity.

    more tokens wins first. the full name is a better label than the short
    form even when the short form appears more often, because a reader
    seeing الأسد cannot tell which one it is while بشار الأسد is
    unambiguous. frequency only breaks ties between forms of equal length.
    """
    counts = Counter(raw_text[m] for m in members)
    return max(counts, key=lambda name: (len(name.split()), counts[name], len(name)))


def _as_mention(row: MentionORM) -> Mention:
    return Mention(
        id=row.id,
        document_id=row.document_id,
        text=row.text,
        start=row.start_offset,
        end=row.end_offset,
        object_type=row.object_type,
        extractor_version_id=row.extractor_version_id,
        retracted=row.retracted,
    )


def resolve_all(
    session: Session,
    ontology: Ontology,
    adapter: ArabicAdapter | None = None,
    scorer: PairScorer | None = None,
    max_block_size: int = 100,
) -> ResolveStats:
    adapter = adapter or ArabicAdapter()
    scorer = scorer or PairScorer()
    stats = ResolveStats()

    contexts = load_mention_contexts(session, adapter)
    stats.mentions = len(contexts)
    if not contexts:
        return stats

    mention_rows = {
        row.id: row
        for row in session.scalars(
            select(MentionORM).where(MentionORM.id.in_(list(contexts)))
        ).all()
    }
    raw_text = {mid: row.text for mid, row in mention_rows.items()}

    # collapse exact duplicates BEFORE blocking. this turned out to matter a
    # lot and I missed it on the first pass.
    #
    # إيران appears 476 times in the corpus. every one of those mentions
    # normalizes to the same string, so blocking put all 476 in one block,
    # the block blew past max_block_size, and the whole thing got dropped as
    # oversized. the most mentioned entities in the corpus were the ones
    # most likely to be skipped entirely, and they came out as 476 separate
    # singleton entities. the first production run produced 3704 singletons
    # out of 3769 entities for exactly this reason.
    #
    # two mentions of the same type whose normalized text is byte identical
    # are the same thing. that needs no model and no threshold. so I group
    # them up front, run the expensive machinery on ONE representative per
    # distinct surface form, and expand back at the end. blocks shrink from
    # hundreds of members to a handful and nothing gets dropped.
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for mention_id, context in contexts.items():
        groups[(context.object_type, context.normalized_name)].append(mention_id)

    representatives = {members[0]: members for members in groups.values()}
    stats.exact_duplicate_groups = len(representatives)

    # block first. a pair that shares no key never gets scored.
    blocker = KeyBlocker(max_block_size=max_block_size)
    candidates = blocker.candidate_pairs(
        {mid: set(contexts[mid].blocking_keys) for mid in representatives}
    )
    stats.candidate_pairs = len(candidates)
    stats.full_pairs = blocker.last_stats.full_pairs

    scores: dict[tuple[int, int], float] = {}
    matched: list[tuple[int, int]] = []
    for a, b in candidates:
        ca, cb = contexts[a], contexts[b]
        # never merge across object types. a person and a place sharing a
        # name is a coincidence and not an identity.
        if ca.object_type != cb.object_type:
            continue
        probability = scorer.probability(compute_features(ca, cb))
        scores[(a, b)] = probability
        if probability >= scorer.weights.threshold:
            matched.append((a, b))

    stats.matched_pairs = len(matched)

    def similarity(x: int, y: int) -> float:
        return scores.get(tuple(sorted((x, y))), 0.0)

    result = cluster_pairs(
        representatives.keys(),
        matched,
        similarity=similarity,
        threshold=scorer.weights.threshold,
    )
    stats.giant_components_split = result.giant_components_split
    stats.size_histogram = result.size_histogram

    extractor = register_extractor_version(
        session,
        EXTRACTOR_NAME,
        EXTRACTOR_VERSION,
        description="Multi-key blocking, logistic regression pair scorer, union find with complete linkage split",
    )

    # retract the previous generation before writing the new one. P6 says
    # retract and never delete so last week's answer stays on record.
    previous = session.scalars(select(EntityORM).where(EntityORM.retracted.is_(False))).all()
    for row in previous:
        row.retracted = True
        row.retracted_reason = f"superseded by {EXTRACTOR_NAME} v{EXTRACTOR_VERSION}"
    stats.entities_retracted = len(previous)

    for representative_cluster in result.clusters:
        # expand each representative back into every mention that shared its
        # exact normalized form
        members = [m for rep in representative_cluster for m in representatives[rep]]
        entity = create_entity(
            session,
            object_type=contexts[members[0]].object_type,
            canonical_name=pick_canonical_name(members, raw_text),
            properties={
                "mention_count": len(members),
                "surface_forms": sorted({raw_text[m] for m in members}),
            },
            source_mention=_as_mention(mention_rows[members[0]]),
            extractor_version=extractor,
            ontology=ontology,
        )
        for other in members[1:]:
            add_entity_evidence(session, entity, _as_mention(mention_rows[other]), extractor)
        stats.entities_created += 1

    return stats


def run_core_resolution(max_block_size: int = 100) -> ResolveStats:
    ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
    with get_core_session() as session:
        return resolve_all(session, ontology, max_block_size=max_block_size)
