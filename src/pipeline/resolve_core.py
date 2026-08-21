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
from src.extract.gazetteer import GazetteerExtractor
from src.resolve.blocking import KeyBlocker
from src.resolve.cluster import cluster_pairs
from src.resolve.features import MentionContext, compute_features
from src.resolve.review import enqueue_review_pair, latest_decisions, ordered_pair
from src.resolve.scorer import PairScorer
from src.resolve.stable_entities import acquire_resolution_output_lock
from src.store.database import get_core_session
from src.store.orm import DocumentORM, EntityORM, MentionORM
from src.store.provenance import add_entity_evidence, create_entity, register_extractor_version

logger = logging.getLogger("pipeline.resolve_core")

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "config" / "ontology.yaml"

EXTRACTOR_NAME = "pair_scorer_resolver"
# 1.1.0 adds persisted human must-link/cannot-link constraints and the review
# queue.  Retraining config/pair_scorer_weights.json must also bump this
# version so a changed scorer produces a new immutable queue snapshot (P4).
EXTRACTOR_VERSION = "1.1.0"


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
    review_pairs_queued: int = 0
    human_constraints_applied: int = 0
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


def pick_canonical_name(
    members: list[int], raw_text: dict[int, str], gazetteer_name: str | None = None
) -> str:
    """decide what to call the merged entity.

    if the gazetteer named this thing I use its name and stop. it picked
    علي خامنئي as the canonical form and my frequency heuristic was
    overriding that with المرشد الأعلى, which is a job title and not a name.
    the dictionary already made this call deliberately and second guessing
    it with a word count was worse.

    for everything else, more tokens wins first. the full name beats the
    short form even when the short form is more common, because a reader
    seeing الأسد cannot tell which one it is while بشار الأسد is
    unambiguous. frequency only breaks ties between forms of equal length.
    """
    if gazetteer_name:
        return gazetteer_name

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


def _partition_on_cannot_links(
    members: list[int], cannot_links: set[tuple[int, int]]
) -> list[list[int]]:
    """Split one exact/gazetteer group enough to honor human cannot-links.

    Exact spelling is normally collapsed before blocking, but two people can
    share the same written name.  A manual split must therefore run before
    that collapse.  Greedy placement is deliberately simple: each mention
    joins the first subgroup containing nobody it is forbidden to match.
    """
    partitions: list[list[int]] = []
    for mention_id in sorted(members):
        for partition in partitions:
            if all(ordered_pair(mention_id, other) not in cannot_links for other in partition):
                partition.append(mention_id)
                break
        else:
            partitions.append([mention_id])
    return partitions


def _retract_live_entities(session: Session) -> int:
    """Retire the previous disposable resolver generation without deleting it."""

    previous = session.scalars(
        select(EntityORM).where(EntityORM.retracted.is_(False))
    ).all()
    for row in previous:
        row.retracted = True
        row.retracted_reason = f"superseded by {EXTRACTOR_NAME} v{EXTRACTOR_VERSION}"
    return len(previous)


def resolve_all(
    session: Session,
    ontology: Ontology,
    adapter: ArabicAdapter | None = None,
    scorer: PairScorer | None = None,
    max_block_size: int = 100,
    gazetteer: GazetteerExtractor | None = None,
    review_margin: float = 0.15,
    review_limit: int = 20,
) -> ResolveStats:
    if review_margin < 0:
        raise ValueError("review_margin must be non-negative")
    if review_limit < 0:
        raise ValueError("review_limit must be non-negative")
    # Stable observation shares this transaction-scoped PostgreSQL lock.  It
    # protects the legacy output from being read halfway through retraction
    # and rebuilding, but does not activate any stable-entity behavior.
    # ``session.execute`` in the PostgreSQL advisory-lock helper would trigger
    # autoflush by default.  Keep caller-pending entity changes behind the
    # shared lock, then deliberately flush them below.
    with session.no_autoflush:
        acquire_resolution_output_lock(session)
    session.flush()
    adapter = adapter or ArabicAdapter()
    scorer = scorer or PairScorer()
    gazetteer = gazetteer if gazetteer is not None else GazetteerExtractor()
    stats = ResolveStats()

    contexts = load_mention_contexts(session, adapter)
    stats.mentions = len(contexts)
    if not contexts:
        # An all-retracted corpus is still a resolver generation.  Retiring
        # its prior disposable entities lets an explicit observer record an
        # honest all-absent stable snapshot instead of seeing stale output.
        stats.entities_retracted = _retract_live_entities(session)
        return stats

    mention_rows = {
        row.id: row
        for row in session.scalars(
            select(MentionORM).where(MentionORM.id.in_(list(contexts)))
        ).all()
    }
    raw_text = {mid: row.text for mid, row in mention_rows.items()}
    decisions = latest_decisions(session)
    mention_cannot_links = {
        pair for pair, decision in decisions.items() if decision.decision == "different"
    }

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
    # the grouping key is the gazetteer's canonical name when it has one,
    # otherwise the normalized surface form. so every alias of a known name
    # lands in one group without the scorer being involved: ترامب, ترمب,
    # دونالد ترامب and دونالد ترمب are four surface forms the dictionary
    # already declares to be one person, and rediscovering that with fuzzy
    # matching would be solving a problem I had the answer to.
    #
    # unknown names still group only with byte identical twins and go to the
    # learned scorer from there. same split as M3: dictionary where it knows,
    # model where it does not.
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for mention_id, context in contexts.items():
        known = gazetteer.canonical_for(context.normalized_name) if gazetteer else None
        # only trust the dictionary when it agrees with how the mention was
        # typed. سوريا is a location in the gazetteer, so a mention tagged
        # person with that text is a disagreement and not an identity, and
        # taking the dictionary's word for it would merge a person into a
        # country. on disagreement I fall back to grouping by surface form.
        if known and known[0] != context.object_type:
            known = None
        key = known if known else (context.object_type, context.normalized_name)
        groups[key].append(mention_id)

    partitioned_groups = [
        (key, partition)
        for key, members in groups.items()
        for partition in _partition_on_cannot_links(members, mention_cannot_links)
    ]
    representatives = {members[0]: members for _key, members in partitioned_groups}
    representative_for_mention = {
        mention_id: representative
        for representative, members in representatives.items()
        for mention_id in members
    }
    # remember which groups the dictionary named so the entity keeps that
    # name instead of one derived from surface frequency
    gazetteer_names = {
        members[0]: key[1]
        for key, members in partitioned_groups
        if gazetteer and gazetteer.canonical_for(contexts[members[0]].normalized_name) == key
    }
    stats.exact_duplicate_groups = len(representatives)

    accepted_pairs: set[tuple[int, int]] = set()
    rejected_pairs: set[tuple[int, int]] = set()
    for (left_id, right_id), decision in decisions.items():
        if left_id not in representative_for_mention or right_id not in representative_for_mention:
            continue
        left_rep = representative_for_mention[left_id]
        right_rep = representative_for_mention[right_id]
        if left_rep == right_rep:
            continue
        pair = ordered_pair(left_rep, right_rep)
        if decision.decision == "same":
            accepted_pairs.add(pair)
        else:
            rejected_pairs.add(pair)
    stats.human_constraints_applied = len(accepted_pairs) + len(rejected_pairs)

    # block first. a pair that shares no key never gets scored.
    blocker = KeyBlocker(max_block_size=max_block_size)
    candidates = blocker.candidate_pairs(
        {mid: set(contexts[mid].blocking_keys) for mid in representatives}
    )
    stats.candidate_pairs = len(candidates)
    stats.full_pairs = blocker.last_stats.full_pairs

    resolver_version = register_extractor_version(
        session,
        EXTRACTOR_NAME,
        EXTRACTOR_VERSION,
        description="Multi-key blocking, logistic regression pair scorer, union find with complete linkage split",
    )

    scores: dict[tuple[int, int], float] = {}
    matched: set[tuple[int, int]] = set(accepted_pairs)
    scored_candidates = []
    for a, b in candidates:
        ca, cb = contexts[a], contexts[b]
        # never merge across object types. a person and a place sharing a
        # name is a coincidence and not an identity.
        if ca.object_type != cb.object_type:
            continue
        features = compute_features(ca, cb)
        probability = scorer.probability(features)
        pair = ordered_pair(a, b)
        scores[pair] = probability
        scored_candidates.append((ca, cb, probability, features))
        if probability >= scorer.weights.threshold and pair not in rejected_pairs:
            matched.add(pair)

    # A fixed uncertainty band can be empty when a poorly calibrated model
    # is confidently wrong about the whole production distribution.  That is
    # exactly when labels are most valuable.  Prefer pairs inside the band,
    # then fill the remaining review budget with the closest scores outside
    # it so the queue can never be empty while candidates exist.
    ranked_for_review = sorted(
        scored_candidates,
        key=lambda item: abs(item[2] - scorer.weights.threshold),
    )
    in_band = [
        item
        for item in ranked_for_review
        if abs(item[2] - scorer.weights.threshold) <= review_margin
    ]
    selected_for_review = in_band[:review_limit]
    if len(selected_for_review) < review_limit:
        selected_ids = {(item[0].mention_id, item[1].mention_id) for item in selected_for_review}
        for item in ranked_for_review:
            pair_ids = (item[0].mention_id, item[1].mention_id)
            if pair_ids in selected_ids:
                continue
            selected_for_review.append(item)
            selected_ids.add(pair_ids)
            if len(selected_for_review) >= review_limit:
                break

    for ca, cb, probability, features in selected_for_review:
        if enqueue_review_pair(
            session,
            ca,
            cb,
            probability,
            scorer.weights.threshold,
            features,
            resolver_version,
        ):
            stats.review_pairs_queued += 1

    for pair in accepted_pairs:
        scores[pair] = 1.0
    for pair in rejected_pairs:
        scores[pair] = 0.0

    stats.matched_pairs = len(matched)

    def similarity(x: int, y: int) -> float:
        return scores.get(tuple(sorted((x, y))), 0.0)

    result = cluster_pairs(
        representatives.keys(),
        matched,
        similarity=similarity,
        threshold=scorer.weights.threshold,
        cannot_link_pairs=rejected_pairs,
    )
    stats.giant_components_split = result.giant_components_split
    stats.size_histogram = result.size_histogram

    # retract the previous generation before writing the new one. P6 says
    # retract and never delete so last week's answer stays on record.
    stats.entities_retracted = _retract_live_entities(session)

    for representative_cluster in result.clusters:
        # expand each representative back into every mention that shared its
        # exact normalized form
        members = [m for rep in representative_cluster for m in representatives[rep]]
        entity = create_entity(
            session,
            object_type=contexts[members[0]].object_type,
            canonical_name=pick_canonical_name(
                members, raw_text, gazetteer_names.get(representative_cluster[0])
            ),
            properties={
                "mention_count": len(members),
                "surface_forms": sorted({raw_text[m] for m in members}),
            },
            source_mention=_as_mention(mention_rows[members[0]]),
            extractor_version=resolver_version,
            ontology=ontology,
        )
        for other in members[1:]:
            add_entity_evidence(session, entity, _as_mention(mention_rows[other]), resolver_version)
        stats.entities_created += 1

    return stats


def run_core_resolution(
    max_block_size: int = 100,
    review_margin: float = 0.15,
    review_limit: int = 20,
) -> ResolveStats:
    ontology = Ontology.from_yaml(_ONTOLOGY_PATH)
    with get_core_session() as session:
        return resolve_all(
            session,
            ontology,
            max_block_size=max_block_size,
            review_margin=review_margin,
            review_limit=review_limit,
        )
