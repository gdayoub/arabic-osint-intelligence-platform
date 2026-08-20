"""Closed vocabularies shared by the operational ledger and its schema."""

from __future__ import annotations

from enum import StrEnum


class PipelineEventType(StrEnum):
    """Facts that may be appended to the operational event stream."""

    RUN_STARTED = "run_started"
    RUN_HEARTBEAT = "run_heartbeat"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_ABANDONED = "run_abandoned"

    STAGE_STARTED = "stage_started"
    STAGE_SUCCEEDED = "stage_succeeded"
    STAGE_FAILED = "stage_failed"

    SOURCE_STARTED = "source_started"
    SOURCE_SUCCEEDED = "source_succeeded"
    SOURCE_FAILED = "source_failed"

    # Release candidates and promotions are also append-only observations.
    # Reserving the complete vocabulary in the first ledger migration avoids
    # rewriting its database CHECK constraint immediately after adoption.
    RELEASE_RESERVED = "release_reserved"
    RELEASE_CANDIDATE_CREATED = "release_candidate_created"
    PROMOTION_STARTED = "promotion_started"
    RELEASE_PUBLISHED = "release_published"
    RELEASE_FAILED = "release_failed"
    RELEASE_SUPERSEDED = "release_superseded"


class PipelineReasonCode(StrEnum):
    """Safe, finite explanations suitable for a public health projection.

    Exceptions and arbitrary strings never enter this field.  Private runner
    logs can retain debugging detail; the ledger keeps a stable category that
    cannot accidentally expose a URL credential, response body, or traceback.
    """

    UNEXPECTED_ERROR = "unexpected_error"
    UPSTREAM_STAGE_FAILED = "upstream_stage_failed"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    SOURCE_SELECTOR_FAILED = "source_selector_failed"
    SOURCE_PARSE_FAILED = "source_parse_failed"
    SOURCE_ZERO_YIELD = "source_zero_yield"
    DATA_STALE = "data_stale"
    COUNT_INVARIANT_FAILED = "count_invariant_failed"
    LEASE_EXPIRED = "lease_expired"
    RELEASE_CONTRACT_FAILED = "release_contract_failed"
    RELEASE_PUBLISH_FAILED = "release_publish_failed"


PIPELINE_EVENT_TYPES = tuple(item.value for item in PipelineEventType)
PIPELINE_REASON_CODES = tuple(item.value for item in PipelineReasonCode)


# The values are intentionally static.  Health output may include these
# sentences, but it may never interpolate an exception or another free-form
# value into them.
SAFE_REASON_MESSAGES: dict[PipelineReasonCode, str] = {
    PipelineReasonCode.UNEXPECTED_ERROR: "A pipeline operation failed.",
    PipelineReasonCode.UPSTREAM_STAGE_FAILED: "A required earlier stage failed.",
    PipelineReasonCode.SOURCE_FETCH_FAILED: "The source could not be fetched.",
    PipelineReasonCode.SOURCE_SELECTOR_FAILED: (
        "The source page did not match its configured selectors."
    ),
    PipelineReasonCode.SOURCE_PARSE_FAILED: (
        "One or more fetched articles could not be parsed."
    ),
    PipelineReasonCode.SOURCE_ZERO_YIELD: (
        "The source completed without producing an article."
    ),
    PipelineReasonCode.DATA_STALE: (
        "The newest successful article is older than the freshness limit."
    ),
    PipelineReasonCode.COUNT_INVARIANT_FAILED: "A data-count consistency check failed.",
    PipelineReasonCode.LEASE_EXPIRED: (
        "The run stopped reporting progress before its lease deadline."
    ),
    PipelineReasonCode.RELEASE_CONTRACT_FAILED: (
        "The release candidate failed contract validation."
    ),
    PipelineReasonCode.RELEASE_PUBLISH_FAILED: "The release could not be published.",
}


def safe_reason_message(reason_code: PipelineReasonCode | str | None) -> str | None:
    """Return only the prewritten message for a known reason code."""
    if reason_code is None:
        return None
    code = PipelineReasonCode(reason_code)
    return SAFE_REASON_MESSAGES[code]
