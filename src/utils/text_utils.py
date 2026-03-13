"""Text utility helpers for dashboard rendering."""

from __future__ import annotations


def safe_truncate(text: str, max_chars: int = 220) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
