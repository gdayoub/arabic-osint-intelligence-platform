"""tests for the pair scorer and its features."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.lang.arabic import ArabicAdapter
from src.resolve.features import MentionContext, PairFeatures, compute_features, jaccard, jaro_winkler
from src.resolve.scorer import PairScorer, ScorerWeights, sigmoid

ar = ArabicAdapter()
BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def ctx(name, *, others=(), source="AlJazeeraArabic", days=0, object_type="person"):
    return MentionContext(
        mention_id=0,
        normalized_name=ar.normalize(name),
        object_type=object_type,
        document_id=0,
        source=source,
        published_at=BASE + timedelta(days=days),
        blocking_keys=frozenset(ar.blocking_keys(name)),
        co_mentions=frozenset(ar.normalize(o) for o in others),
    )


def test_jaro_winkler_bounds():
    assert jaro_winkler("محمد", "محمد") == 1.0
    assert jaro_winkler("", "محمد") == 0.0
    assert 0.0 <= jaro_winkler("حسن", "حسين") <= 1.0


def test_jaro_winkler_rewards_a_shared_prefix():
    """names diverging at the end are likelier the same than names diverging
    at the start. that is the whole reason I used this over edit distance."""
    assert jaro_winkler("محمود", "محمو") > jaro_winkler("محمد", "احمد")


def test_string_similarity_alone_cannot_separate_the_hard_cases():
    """documents the exact problem the learned weights exist to solve.

    a same-person pair and a different-person pair score almost identically
    on the name alone so no threshold on this feature can split them.
    """
    same_person = jaro_winkler(ar.normalize("بشار الأسد"), ar.normalize("بشار الاسد"))
    different_people = jaro_winkler(ar.normalize("حسن"), ar.normalize("حسين"))
    assert abs(same_person - different_people) < 0.10


def test_jaccard():
    assert jaccard({1, 2}, {1, 2}) == 1.0
    assert jaccard({1}, {2}) == 0.0
    assert jaccard(set(), set()) == 0.0
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)


def test_missing_dates_score_as_no_information_not_as_distant():
    """0.5 and not 0.0. an unknown date must not look like evidence against."""
    a = MentionContext(0, "محمد", "person", 0, "s", None, frozenset(), frozenset())
    b = MentionContext(0, "محمد", "person", 0, "s", None, frozenset(), frozenset())
    assert compute_features(a, b).temporal_proximity == 0.5


def test_temporal_proximity_decays_with_distance():
    near = compute_features(ctx("محمد", days=0), ctx("محمد", days=1)).temporal_proximity
    far = compute_features(ctx("محمد", days=0), ctx("محمد", days=25)).temporal_proximity
    assert near > far
    assert compute_features(ctx("محمد", days=0), ctx("محمد", days=400)).temporal_proximity == 0.0


def test_feature_vector_order_matches_the_declared_names():
    """the guard against a weight silently lining up with the wrong feature."""
    features = compute_features(ctx("محمد"), ctx("احمد"))
    assert len(features.as_vector()) == len(PairFeatures.names())
    assert PairFeatures.names()[0] == "name_similarity"


def test_sigmoid_does_not_overflow_on_large_inputs():
    assert sigmoid(-1000) == pytest.approx(0.0)
    assert sigmoid(1000) == pytest.approx(1.0)
    assert sigmoid(0) == 0.5


def test_shipped_weights_load_and_match_the_current_features():
    weights = ScorerWeights.load()
    assert weights.feature_names == tuple(PairFeatures.names())
    assert len(weights.coefficients) == len(PairFeatures.names())
    assert 0.0 < weights.threshold < 1.0


def test_loading_stale_weights_fails_loudly(tmp_path):
    """if someone adds a feature and forgets to retrain, the coefficients
    would line up with the wrong columns. better to refuse to load."""
    bad = tmp_path / "w.json"
    bad.write_text(
        json.dumps(
            {
                "feature_names": ["only_one_feature"],
                "coefficients": [1.0],
                "intercept": 0.0,
                "threshold": 0.6,
                "trained_at": "2026-01-01T00:00:00Z",
                "training_pairs": 1,
                "auc": 0.5,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="retrain"):
        ScorerWeights.load(bad)


def test_scorer_separates_a_variant_pair_from_a_confusable_pair():
    """end to end on the two cases that matter."""
    scorer = PairScorer()
    variant = scorer.probability(compute_features(ctx("بشار الأسد"), ctx("بشار الاسد")))
    confusable = scorer.probability(compute_features(ctx("حسن"), ctx("حسين")))
    assert variant > confusable


def test_explain_returns_a_contribution_per_feature():
    """what makes a merge defensible to a human."""
    scorer = PairScorer()
    parts = scorer.explain(compute_features(ctx("بشار الأسد"), ctx("بشار الاسد")))
    assert [name for name, _, _ in parts] == PairFeatures.names()
