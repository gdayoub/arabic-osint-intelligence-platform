"""Al Chourouk (Tunisia) Arabic scraper implementation."""

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
    normalize_whitespace,
)


def _parse_epoch_meta(soup: BeautifulSoup) -> datetime | None:
    """article:published_time here is a raw Unix-epoch string, not ISO-8601."""
    node = soup.select_one("meta[property='article:published_time']")
    if node is None:
        return None
    raw = node.get("content")
    if not raw or not raw.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OSError):
        return None

# e.g. /article/<arabic-slug>
ARTICLE_PATH_RE = re.compile(r"^/article/.+")

# PDF-edition landing pages share the /article/ prefix but only carry
# masthead boilerplate, not real article bodies -- exclude them by slug.
PDF_EDITION_MARKER = "جريدة-الشروق-ليوم"


class AlChouroukScraper(BaseScraper):
    """Scraper for Al Chourouk (www.alchourouk.com).

    TODO(selector-maintenance): if the site updates layout,
    adjust selectors inside `extract_article_links` and `parse_article`.
    """

    NAME = "alchourouk_scraper"
    VERSION = "1.0.0"

    def __init__(self, settings: Settings | None = None):
        app_settings = settings or SETTINGS
        super().__init__(
            source_name="AlChourouk",
            base_url="https://www.alchourouk.com",
            start_urls=app_settings.alchourouk_seed_urls or [
                "https://www.alchourouk.com",
            ],
            settings=app_settings,
        )

    def is_valid_article_url(self, article_url: str) -> bool:
        path = unquote(urlparse(article_url).path)
        if PDF_EDITION_MARKER in path:
            return False
        return bool(ARTICLE_PATH_RE.match(path))

    def is_valid_listing_url(self, listing_url: str) -> bool:
        parsed = urlparse(listing_url)
        return not parsed.netloc or parsed.netloc in ("www.alchourouk.com", "alchourouk.com")

    def extract_article_links(self, listing_html: str) -> list[str]:
        soup: BeautifulSoup = self.to_soup(listing_html)
        links: set[str] = set()

        for el in soup.select("a[href]"):
            href = (el.get("href") or "").strip()
            path = unquote(urlparse(href).path if href.startswith("http") else href)
            if ARTICLE_PATH_RE.match(path) and PDF_EDITION_MARKER not in path:
                links.add(href)

        return sorted(links)

    def parse_article(self, article_url: str, article_html: str) -> ArticleRecord | None:
        soup: BeautifulSoup = self.to_soup(article_html)

        title_node = soup.select_one("h1.page-header [property='schema:name']") or soup.select_one(
            "h1.page-header"
        )
        title = normalize_whitespace(title_node.get_text(" ", strip=True)) if title_node else ""

        # [property='schema:text'] uniquely marks the real article body --
        # the same field--name-body classes also appear on sidebar widgets
        # (social links, newsletter box) elsewhere on the page.
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.select("div[property='schema:text'] p")
        ]
        body = normalize_whitespace(" ".join(body_parts))

        author_node = soup.select_one("[rel='author']") or soup.select_one(".field--name-field-auteur")
        author = normalize_whitespace(author_node.get_text(" ", strip=True)) if author_node else None

        published_date = _parse_epoch_meta(soup)
        if published_date is None:
            published_date = extract_json_ld_datetime(soup)

        tags = [
            normalize_whitespace(t.get_text(" ", strip=True))
            for t in soup.select("a[href*='/tags/']")
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
