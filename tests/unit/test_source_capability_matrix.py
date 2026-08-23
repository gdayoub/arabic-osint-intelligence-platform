"""The capability matrix must never drift from the real scraper registry.

This is a fixture test, not a scraper test: it locks
``tests/fixtures/runtime/source_capability_matrix.json`` (the frozen
Intelligence Atlas baseline, Task 1) to whatever
``configured_ingest_source_names()`` actually returns today. If a source is
added, removed, or renamed in ``src/pipeline/ingest_core.py::build_scrapers``
without the fixture being regenerated, this test fails loudly instead of the
matrix silently going stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.ops.runtime import configured_ingest_source_names

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "runtime"
    / "source_capability_matrix.json"
)

# Sources that were deliberately removed from the scraper registry and must
# never silently reappear in the matrix. Al Arabiya was removed in commit
# e7a51a9 ("Remove Al Arabiya: permanently blocked by Cloudflare bot
# management") — its presence here would mean the matrix was hand-edited
# from stale memory rather than regenerated from the live registry.
REJECTED_SOURCES = frozenset({"AlArabiya"})

REQUIRED_FIELDS = frozenset(
    {
        "source_name",
        "configured",
        "live_attempted",
        "body_paragraphs_preserved",
        "published_date_quality",
        "robots_policy_checked",
        "state_media_metadata",
        "current_public_document_count",
        "last_attempt_duration_seconds",
        "last_yield",
        "last_failure_reason",
    }
)


def _load_matrix() -> dict:
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_fixture_file_exists():
    assert FIXTURE_PATH.is_file(), (
        f"expected the capability matrix fixture at {FIXTURE_PATH}; "
        "it is generated once and frozen as the Task 1 baseline"
    )


def test_matrix_names_exactly_the_configured_sources():
    matrix = _load_matrix()
    sources = matrix["sources"]

    configured = configured_ingest_source_names()
    matrix_names = [entry["source_name"] for entry in sources]

    # Exactly the same sources, not a superset or subset: a source dropped
    # from build_scrapers() must disappear from the matrix, and a source
    # added there must be present before the matrix can be trusted again.
    assert set(matrix_names) == set(configured)
    # No source is recorded twice under different casing or spacing.
    assert len(matrix_names) == len(set(matrix_names))
    # And the live registry itself has 21 sources today per the Task 1 brief.
    assert len(configured) == 21


def test_matrix_contains_no_rejected_source():
    matrix = _load_matrix()
    matrix_names = {entry["source_name"] for entry in matrix["sources"]}

    assert matrix_names.isdisjoint(REJECTED_SOURCES)


def test_every_entry_records_every_interface_field():
    matrix = _load_matrix()

    for entry in matrix["sources"]:
        missing = REQUIRED_FIELDS - entry.keys()
        assert not missing, f"{entry.get('source_name')} is missing {missing}"
        extra = entry.keys() - REQUIRED_FIELDS
        assert not extra, f"{entry.get('source_name')} has undeclared fields {extra}"


def test_every_configured_source_is_marked_configured():
    matrix = _load_matrix()

    for entry in matrix["sources"]:
        assert entry["configured"] is True


def test_unmeasured_fields_are_null_not_guessed():
    """A source with zero live evidence must record null, never an inferred
    success. Only current_public_document_count is always known (it is read
    directly from the live public snapshot, where an absent source counts as
    zero), and only configured/source_name are always known (they come from
    the scraper registry itself). Every other field is either a real
    recorded observation or explicitly null.
    """
    matrix = _load_matrix()

    for entry in matrix["sources"]:
        assert isinstance(entry["current_public_document_count"], int)
        assert entry["current_public_document_count"] >= 0
        for field in (
            "live_attempted",
            "body_paragraphs_preserved",
            "published_date_quality",
            "robots_policy_checked",
            "state_media_metadata",
            "last_attempt_duration_seconds",
            "last_yield",
            "last_failure_reason",
        ):
            value = entry[field]
            assert value is None or isinstance(value, (bool, int, float, str)), (
                f"{entry['source_name']}.{field} must be null or a plain "
                "recorded value, never an inferred object"
            )


def test_public_document_counts_match_the_recorded_public_snapshot():
    """The three sources represented in the frozen public snapshot (see
    docs/execution/intelligence-atlas/README.md and
    benchmarks/intelligence_atlas_baseline.md) must show nonzero counts;
    every other configured source shows zero, since it never reached the
    public artifact even though it is registered in build_scrapers().
    """
    matrix = _load_matrix()
    by_name = {entry["source_name"]: entry for entry in matrix["sources"]}

    represented = {"AlJazeeraArabic", "BBCArabic", "CNNArabic"}
    for name, entry in by_name.items():
        if name in represented:
            assert entry["current_public_document_count"] > 0
        else:
            assert entry["current_public_document_count"] == 0
