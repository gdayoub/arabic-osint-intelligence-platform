"""Wafa News Agency (Palestine) Arabic scraper implementation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from src.config.settings import SETTINGS, Settings
from src.scraping.base_scraper import BaseScraper
from src.scraping.scraper_utils import (
    ArticleRecord,
    build_content_hash,
    extract_json_ld_datetime,
    extract_meta_datetime,
    normalize_whitespace,
)

# e.g. /news/2026/8/22/<arabic-slug>-152586
ARTICLE_PATH_RE = re.compile(r"^/news/\d{4}/\d{1,2}/\d{1,2}/.+-\d+$")

# "تاريخ النشر: 22/08/2026 08:26 ص" -- DD/MM/YYYY HH:MM + ص (AM) / م (PM).
# No <time> tag or meta date exists on this site; this plain-text field is
# the only publish timestamp available.
PUBLISH_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([صم])"
)


def _parse_wafa_date(text: str | None) -> datetime | None:
    if not text:
        return None
    match = PUBLISH_DATE_RE.search(text)
    if not match:
        return None
    day, month, year, hour, minute, meridiem = match.groups()
    hour_i = int(hour)
    if meridiem == "م" and hour_i != 12:
        hour_i += 12
    elif meridiem == "ص" and hour_i == 12:
        hour_i = 0
    try:
        return datetime(
            int(year), int(month), int(day), hour_i, int(minute), tzinfo=timezone.utc
        )
    except ValueError:
        return None


class WafaScraper(BaseScraper):
    """Scraper for Wafa News Agency (wafa.ps).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "wafa_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="Wafa",
            base_url="https://www.wafa.ps",
            start_urls=app_settings.wafa_seed_urls or [
                "https://www.wafa.ps",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        path = unquote(urlparse(article_url).path)
        return bool(ARTICLE_PATH_RE.match(path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("www.wafa.ps", "wafa.ps")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = unquote(urlparse(href).path if href.startswith("http") else href)
            if ARTICLE_PATH_RE.match(path):
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h3.title") or soup.select_one("h1")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        body_parts = [
            p.get_text(" ", strip=True) for p in soup.select("div.content p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            published_date = extract_meta_datetime(soup)
        if published_date is None:
            date_node = soup.select_one("span.meta-itemin.date")
            date_text = date_node.get_text(" ", strip=True) if date_node else None
            published_date = _parse_wafa_date(date_text)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[rel='tag']")
            if t.get_text(strip=True)
        ]

        path_parts = [p for p in unquote(urlparse(article_url).path).split("/") if p]
        source_section = path_parts[0] if path_parts else None

        if not title or not body:
            return None

        return ArticleRecord(
            source=self.source_name,
            title=title,
            subtitle=None,
            body=body,
            author=author,
            published_date=published_date,
            url=article_url,
            tags=tags,
            source_section=source_section,
            content_hash=build_content_hash(title, body, article_url),
        )
