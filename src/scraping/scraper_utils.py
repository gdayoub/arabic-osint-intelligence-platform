"""Shared helpers and datatypes for source scrapers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from dateutil import parser as date_parser


@dataclass(slots=True)
class ArticleRecord:
    source: str
    title: str
    subtitle: str | None
    body: str
    author: str | None
    published_date: datetime | None
    url: str
    tags: list[str] = field(default_factory=list)
    source_section: str | None = None
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""

    def to_dict(self) -> dict:
        canonical_url = canonicalize_url(self.url)
        return {
            "source": self.source,
            "title": self.title,
            "subtitle": self.subtitle,
            "body": self.body,
            "author": self.author,
            "published_date": self.published_date,
            "url": canonical_url,
            "tags": self.tags,
            "source_section": self.source_section,
            "collected_at": self.collected_at,
            "content_hash": self.content_hash
            or build_content_hash(self.title, self.body, canonical_url),
        }


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonicalize_url(url: str) -> str:
    """Normalize URL for deduplication.

    Removes fragment/query-string noise and normalizes trailing slash.
    """
    if not url:
        return ""
    split = urlsplit(url.strip())
    path = split.path.rstrip("/") or "/"
    return urlunsplit((split.scheme, split.netloc.lower(), path, "", ""))


def build_content_hash(title: str, body: str, url: str) -> str:
    payload = f"{title}|{body}|{url}".strip().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def extract_json_ld_objects(soup: BeautifulSoup) -> list[dict]:
    """Parse application/ld+json scripts into dict objects."""
    objects: list[dict] = []
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue

        if isinstance(parsed, list):
            objects.extend([item for item in parsed if isinstance(item, dict)])
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("@graph"), list):
                objects.extend(
                    [item for item in parsed["@graph"] if isinstance(item, dict)]
                )
            objects.append(parsed)

    return objects


def extract_meta_datetime(soup: BeautifulSoup) -> datetime | None:
    """Extract publication datetime from common meta tags."""
    candidates = [
        "meta[property='article:published_time']",
        "meta[name='article:published_time']",
        "meta[property='og:published_time']",
        "meta[name='pubdate']",
        "meta[name='timestamp']",
    ]
    for selector in candidates:
        node = soup.select_one(selector)
        if node:
            value = node.get("content")
            dt = parse_datetime(value)
            if dt:
                return dt
    return None


def extract_json_ld_datetime(soup: BeautifulSoup) -> datetime | None:
    """Extract datetime from JSON-LD article metadata."""
    datetime_keys = ("datePublished", "uploadDate", "dateCreated", "dateModified")
    for obj in extract_json_ld_objects(soup):
        for key in datetime_keys:
            value = obj.get(key)
            if isinstance(value, str):
                dt = parse_datetime(value)
                if dt:
                    return dt
    return None
