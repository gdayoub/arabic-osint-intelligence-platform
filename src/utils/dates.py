"""Date helpers used across scraping and analytics."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_date_label(dt: datetime | None) -> str:
    if dt is None:
        return "Unknown"
    return to_utc(dt).strftime("%Y-%m-%d")
