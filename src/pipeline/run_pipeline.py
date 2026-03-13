"""Orchestrate end-to-end ingestion + processing pipeline."""

from __future__ import annotations

import logging

from src.pipeline.ingest_pipeline import run_ingestion
from src.pipeline.process_pipeline import run_processing

logger = logging.getLogger("pipeline.run")


def run_full_pipeline() -> dict:
    ingestion_stats = run_ingestion()
    processing_stats = run_processing()
    summary = {
        "ingestion": ingestion_stats,
        "processing": processing_stats,
    }
    logger.info("Pipeline completed | %s", summary)
    return summary


if __name__ == "__main__":
    print(run_full_pipeline())
