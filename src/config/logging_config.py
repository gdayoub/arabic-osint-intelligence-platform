"""Structured logging configuration for all services."""

from __future__ import annotations

import logging
import sys


LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(funcName)s:%(lineno)d | %(message)s"
)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger once for scripts and Streamlit app."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
