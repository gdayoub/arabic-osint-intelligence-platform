"""Al Khaleej (UAE) Arabic scraper implementation."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.config.settings import SETTINGS, Settings
from src.scraping.base_scraper import BaseScraper
from src.scraping.scraper_utils import (
    ArticleRecord,
    build_content_hash,
    extract_json_ld_datetime,
    extract_meta_datetime,
    normalize_whitespace,
    parse_datetime,
)

# e.g. /2026-05-03/عرب-وعالم/العالم/العاصفة-القادمة
ARTICLE_PATH_RE = re.compile(r"^/\d{4}-\d{2}-\d{2}/.+")


class AlKhaleejScraper(BaseScraper):
    """Scraper for Al Khaleej Arabic (alkhaleej.ae).

    TODO(selector-maintenance): if Al Khaleej updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "alkhaleej_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlKhaleej",
            base_url="https://www.alkhaleej.ae",
            start_urls=app_settings.alkhaleej_seed_urls or ["https://www.alkhaleej.ae/"],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        return bool(ARTICLE_PATH_RE.match(urlparse(article_url).path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        if parsed.netloc and parsed.netloc not in ("www.alkhaleej.ae", "alkhaleej.ae"):
            return False
        return True

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

        title_node = soup.select_one("h1.article-title")
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        # Body paragraphs are plain divs, not <p> tags, on this theme.
        body_parts = [
            div.get_text(" ", strip=True)
            for div in soup.select("div.article-content-wrapper div.article-text")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".author-name")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = extract_meta_datetime(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)
        if published_date is None:
            time_node = soup.select_one("time[datetime]")
            published_date = parse_datetime(time_node.get("datetime") if time_node else None)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/tags/']")
            if t.get_text(strip=True)
        ]

        # URL shape is /YYYY-MM-DD/<section>/<subsection>/<slug>.
        path_parts = [p for p in urlparse(article_url).path.split("/") if p]
        source_section = path_parts[1] if len(path_parts) > 1 else None

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
