"""Ammon News (Jordan) Arabic scraper implementation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.config.settings import SETTINGS, Settings
from src.scraping.base_scraper import BaseScraper
from src.scraping.scraper_utils import (
    ArticleRecord,
    build_content_hash,
    extract_json_ld_objects,
    extract_meta_datetime,
    normalize_whitespace,
)

# e.g. /article/1021834
ARTICLE_PATH_RE = re.compile(r"^/article/\d+$")

# "<title> | <section> | وكالة عمون الاخبارية" -- everything from the first
# "|" on is site colophon/section labeling, not part of the headline.
TITLE_SUFFIX_RE = re.compile(r"\s*\|.*$")

# JSON-LD datePublished is "DD-MM-YYYY HH:MM AM/PM" (day-month, not
# month-day). dateutil's default American-order guess silently
# misinterprets ambiguous cases like "08-12-2026" as month=08/day=12
# instead of day=08/month=12, so this is parsed explicitly rather than via
# the shared dateutil-based helper.
AMMON_DATE_RE = re.compile(
    r"(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)", re.IGNORECASE
)


def _parse_ammon_date(soup: BeautifulSoup) -> datetime | None:
    for obj in extract_json_ld_objects(soup):
        value = obj.get("datePublished")
        if not isinstance(value, str):
            continue
        match = AMMON_DATE_RE.search(value)
        if not match:
            continue
        day, month, year, hour, minute, meridiem = match.groups()
        hour_i = int(hour)
        if meridiem.upper() == "PM" and hour_i != 12:
            hour_i += 12
        elif meridiem.upper() == "AM" and hour_i == 12:
            hour_i = 0
        try:
            return datetime(
                int(year), int(month), int(day), hour_i, int(minute), tzinfo=timezone.utc
            )
        except ValueError:
            return None
    return None


class AmmonNewsScraper(BaseScraper):
    """Scraper for Ammon News (www.ammonnews.net).

    The site has no <h1> tags at all, so the title comes from <title>
    instead (see docs written after live verification).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "ammonnews_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AmmonNews",
            base_url="https://www.ammonnews.net",
            start_urls=app_settings.ammonnews_seed_urls or [
                "https://www.ammonnews.net",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("www.ammonnews.net", "ammonnews.net")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = urlparse(href).path if href.startswith("http") else href
            if ARTICLE_PATH_RE.match(path):
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("title")
        title = ""
        if title_node:
            raw_title = title_node.get_text(" ", strip=True)
            title = normalize_whitespace(TITLE_SUFFIX_RE.sub("", raw_title))

        content_node = soup.select_one("div#newscontent")
        body = normalize_whitespace(content_node.get_text(" ", strip=True)) if content_node else ""

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = _parse_ammon_date(soup)
        if published_date is None:
            published_date = extract_meta_datetime(soup)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/tags/']")
            if t.get_text(strip=True)
        ]

        path_parts = [p for p in urlparse(article_url).path.split("/") if p]
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
